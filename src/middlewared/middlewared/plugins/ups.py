import csv
import datetime
import dateutil.tz
import glob
import io
import os
import re
import syslog

from middlewared.schema import accepts, Bool, Dict, Int, List, Str
from middlewared.service import private, SystemServiceService, ValidationErrors
from middlewared.utils import run
from middlewared.validators import Email, Range


DRIVER_BIN_DIR = '/usr/local/libexec/nut'


class UPSService(SystemServiceService):
    DRIVERS_AVAILABLE = set(os.listdir(DRIVER_BIN_DIR))

    class Config:
        datastore = 'services.ups'
        datastore_prefix = 'ups_'
        datastore_extend = 'ups.ups_config_extend'
        service = 'ups'
        service_verb = 'restart'

    @private
    async def ups_config_extend(self, data):
        data['mode'] = data['mode'].upper()
        data['shutdown'] = data['shutdown'].upper()
        data['toemail'] = [v for v in data['toemail'].split(';') if v]
        return data

    @accepts()
    async def port_choices(self):
        ports = [x for x in glob.glob('/dev/cua*') if x.find('.') == -1]
        ports.extend(glob.glob('/dev/ugen*'))
        ports.extend(glob.glob('/dev/uhid*'))
        return ports

    @accepts()
    def driver_choices(self):
        """
        Returns choices of UPS drivers supported by the system.
        """
        ups_choices = {}
        if os.path.exists("/conf/base/etc/local/nut/driver.list"):
            with open('/conf/base/etc/local/nut/driver.list', 'rb') as f:
                d = f.read().decode('utf8', 'ignore')
            r = io.StringIO()
            for line in re.sub(r'[ \t]+', ' ', d, flags=re.M).split('\n'):
                r.write(line.strip() + '\n')
            r.seek(0)
            reader = csv.reader(r, delimiter=' ', quotechar='"')
            for row in reader:
                if len(row) == 0 or row[0].startswith('#'):
                    continue
                if row[-2] == '#':
                    last = -3
                else:
                    last = -1
                driver_str = row[last]
                driver_annotation = ''
                m = re.match(r'(.+) \((.+)\)', driver_str)  # "blazer_usb (USB ID 0665:5161)"
                if m:
                    driver_str, driver_annotation = m.group(1), m.group(2)
                for driver in driver_str.split(' or '):  # can be "blazer_ser or blazer_usb"
                    driver = driver.strip()
                    if driver not in self.DRIVERS_AVAILABLE:
                        continue
                    for i, field in enumerate(list(row)):
                        row[i] = field
                    ups_choices['$'.join([driver, row[3]])] = '%s (%s)' % (
                        ' '.join(filter(None, row[0:last])),
                        ', '.join(filter(None, [driver, driver_annotation]))
                    )
        return ups_choices

    @private
    async def validate_data(self, data, schema):
        verrors = ValidationErrors()

        driver = data.get('driver')
        if driver:
            if driver not in (await self.middleware.call('ups.driver_choices')).keys():
                verrors.add(
                    f'{schema}.driver',
                    'Driver selected does not match local machine\'s driver list'
                )

        identifier = data['identifier']
        if identifier:
            if not re.search(r'^[a-z0-9\.\-_]+$', identifier, re.I):
                verrors.add(
                    f'{schema}.identifier',
                    'Use alphanumeric characters, ".", "-" and "_"'
                )

        for field in [field for field in ['monpwd', 'monuser'] if data.get(field)]:
            if re.search(r'[ #]', data[field], re.I):
                verrors.add(
                    f'{schema}.{field}',
                    'Spaces or number signs are not allowed'
                )

        mode = data.get('mode')
        if mode == 'MASTER':
            for field in filter(
                lambda f: not data[f],
                ['port', 'driver']
            ):
                verrors.add(
                    f'{schema}.{field}',
                    'This field is required'
                )
        else:
            if not data.get('remotehost'):
                verrors.add(
                    f'{schema}.remotehost',
                    'This field is required'
                )

        to_emails = data.get('toemail')
        if to_emails:
            data['toemail'] = ';'.join(to_emails)
        else:
            data['toemail'] = ''

        data['mode'] = data['mode'].lower()
        data['shutdown'] = data['shutdown'].lower()

        return verrors, data

    @accepts(
        Dict(
            'ups_update',
            Bool('emailnotify'),
            Bool('powerdown'),
            Bool('rmonitor'),
            Int('nocommwarntime', null=True),
            Int('remoteport'),
            Int('shutdowntimer'),
            Int('hostsync', validators=[Range(min=0)]),
            Str('description'),
            Str('driver'),
            Str('extrausers'),
            Str('identifier', empty=False),
            Str('mode', enum=['MASTER', 'SLAVE']),
            Str('monpwd', empty=False),
            Str('monuser', empty=False),
            Str('options'),
            Str('optionsupsd'),
            Str('port'),
            Str('remotehost'),
            Str('shutdown', enum=['LOWBATT', 'BATT']),
            Str('shutdowncmd', empty=False),
            Str('subject'),
            List('toemail', items=[Str('email', validators=[Email()])]),
            update=True
        )
    )
    async def do_update(self, data):
        """
        Update UPS Service Configuration.

        `emailnotify` when enabled, sends out notifications of different UPS events via email.

        `powerdown` when enabled, sets UPS to power off after shutting down the system.

        `nocommwarntime` is a value in seconds which makes UPS Service wait the specified seconds before alerting that
        the Service cannot reach configured UPS.

        `shutdowntimer` is a value in seconds which tells the Service to wait specified seconds for the UPS before
        initiating a shutdown. This only applies when `shutdown` is set to "BATT".

        `shutdowncmd` is the command which is executed to initiate a shutdown. It defaults to "poweroff".

        `toemail` is a list of valid email id's on which notification emails are sent.
        """
        config = await self.config()
        old_config = config.copy()
        config.update(data)
        verros, config = await self.validate_data(config, 'ups_update')
        if verros:
            raise verros

        old_config['mode'] = old_config['mode'].lower()
        old_config['shutdown'] = old_config['shutdown'].lower()
        old_config['toemail'] = ';'.join(old_config['toemail']) if old_config['toemail'] else ''

        if len(set(old_config.items()) ^ set(config.items())) > 0:
            if config['identifier'] != old_config['identifier']:
                await self.dismiss_alerts()

            await self._update_service(old_config, config)

        return await self.config()

    @private
    async def alerts_mapping(self):
        return {
            'LOWBATT': 'UPSBatteryLow',
            'COMMBAD': 'UPSCommbad',
            'COMMOK': 'UPSCommok',
            'ONBATT': 'UPSOnBattery',
            'ONLINE': 'UPSOnline',
            'REPLBATT': 'UPSReplbatt'
        }

    @private
    async def dismiss_alerts(self):
        config = await self.config()

        for alert in (await self.alerts_mapping()).values():
            await self.middleware.call('alert.oneshot_delete', alert, {'ups': config['identifier']})

    @private
    @accepts(
        Str('notify_type')
    )
    async def upssched_event(self, notify_type):
        config = await self.config()

        if config['mode'] == 'MASTER':
            upsc_identifier = f'{config["identifier"]}@localhost:{config["remoteport"]}'
        else:
            upsc_identifier = f'{config["identifier"]}@{config["remotehost"]}:{config["remoteport"]}'

        if notify_type.lower() == 'shutdown':
            # Before we start FSD with upsmon, lets ensure that ups is not ONLINE (OL).
            # There are cases where battery/charger issues can result in ups.status being "OL LB" at the
            # same time. This will ensure that we don't initiate a shutdown if ups is OL.
            stats_output = (
                await run(
                    '/usr/local/bin/upsc', upsc_identifier,
                    check=False
                )
            ).stdout

            ups_status = re.findall(
                fr'ups.status: (.*)',
                '' if not stats_output else stats_output.decode()
            )
            if ups_status and 'ol' in ups_status[0].lower():
                self.middleware.logger.debug(
                    f'Shutdown not initiated as ups.status ({ups_status[0]}) indicates '
                    f'{config["identifier"]} is ONLINE (OL).'
                )
            else:
                syslog.syslog(syslog.LOG_NOTICE, 'upssched-cmd "issuing shutdown"')
                await run('/usr/local/sbin/upsmon', '-c', 'fsd', check=False)
        elif 'notify' in notify_type.lower():
            # notify_type is expected to be of the following format
            # NOTIFY-EVENT i.e NOTIFY-LOWBATT
            notify_type = notify_type.split('-')[-1]

            # We would like to send alerts for the following events
            alert_mapping = await self.alerts_mapping()

            await self.dismiss_alerts()

            if notify_type in alert_mapping:
                await self.middleware.call(
                    'alert.oneshot_create', alert_mapping[notify_type], {'ups': config['identifier']}
                )

            if config['emailnotify']:
                # Email user with the notification event and details
                # We send the email in the following format ( inclusive line breaks )

                # NOTIFICATION: 'LOWBATT'
                # UPS: 'ups'
                #
                # Statistics recovered:
                #
                # 1) Battery charge (percent)
                # battery.charge: 5
                #
                # 2) Remaining battery level when UPS switches to LB (percent)
                # battery.charge.low: 10
                #
                # 3) Battery runtime (seconds)
                # battery.runtime: 1860
                #
                # 4) Remaining battery runtime when UPS switches to LB (seconds)
                # battery.runtime.low: 900

                ups_name = config['identifier']
                hostname = (await self.middleware.call('system.info'))['hostname']
                current_time = datetime.datetime.now(tz=dateutil.tz.tzlocal()).strftime('%a %b %d %H:%M:%S %Z %Y')
                ups_subject = config['subject'].replace('%d', current_time).replace('%h', hostname)
                body = f'NOTIFICATION: {notify_type!r}<br>UPS: {ups_name!r}<br><br>'

                # Let's gather following stats
                data_points = {
                    'battery.charge': 'Battery charge (percent)',
                    'battery.charge.low': 'Battery level remaining (percent) when UPS switches to Low Battery (LB)',
                    'battery.charge.status': 'Battery charge status',
                    'battery.runtime': 'Battery runtime (seconds)',
                    'battery.runtime.low': 'Battery runtime remaining (seconds) when UPS switches to Low Battery (LB)',
                    'battery.runtime.restart': 'Minimum battery runtime (seconds) to allow UPS restart after power-off',
                }

                stats_output = (
                    await run('/usr/local/bin/upsc', upsc_identifier, check=False)
                ).stdout
                recovered_stats = re.findall(
                    fr'({"|".join(data_points)}): (.*)',
                    '' if not stats_output else stats_output.decode()
                )

                if recovered_stats:
                    body += 'Statistics recovered:<br><br>'
                    # recovered_stats is expected to be a list in this format
                    # [('battery.charge', '5'), ('battery.charge.low', '10'), ('battery.runtime', '1860')]
                    for index, stat in enumerate(recovered_stats):
                        body += f'{index + 1}) {data_points[stat[0]]}<br>  {stat[0]}: {stat[1]}<br><br>'

                else:
                    body += 'Statistics could not be recovered<br>'

                # Subject and body defined, send email
                job = await self.middleware.call(
                    'mail.send', {
                        'subject': ups_subject,
                        'text': body,
                        'to': config['toemail']
                    }
                )

                await job.wait()
                if job.error:
                    self.middleware.logger.debug(f'Failed to send UPS status email: {job.error}')

        else:
            self.middleware.logger.debug(f'Unrecognized UPS notification event: {notify_type}')


async def setup(middleware):
    # Let's delete all UPS related alerts when starting middlewared ensuring we don't have any leftovers
    await middleware.call('ups.dismiss_alerts')
