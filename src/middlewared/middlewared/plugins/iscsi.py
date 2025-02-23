from lxml import etree

from middlewared.async_validators import check_path_resides_within_volume
from middlewared.common.attachment import FSAttachmentDelegate
from middlewared.schema import (accepts, Bool, Dict, IPAddr, Int, List, Patch,
                                Str)
from middlewared.service import (
    CallError, CRUDService, SystemServiceService, ValidationErrors, filterable, private
)
from middlewared.utils import filter_list, run
from middlewared.utils.path import is_child
from middlewared.validators import IpAddress, Range

import bidict
import errno
import hashlib
import re
import os
import subprocess
import sysctl
import uuid

AUTHMETHOD_LEGACY_MAP = bidict.bidict({
    'None': 'NONE',
    'CHAP': 'CHAP',
    'CHAP Mutual': 'CHAP_MUTUAL',
})
RE_IP_PORT = re.compile(r'^(.+?)(:[0-9]+)?$')
RE_TARGET_NAME = re.compile(r'^[-a-z0-9\.:]+$')


class ISCSIGlobalService(SystemServiceService):

    class Config:
        datastore_extend = 'iscsi.global.config_extend'
        datastore_prefix = 'iscsi_'
        service = 'iscsitarget'
        service_model = 'iscsitargetglobalconfiguration'
        namespace = 'iscsi.global'

    @private
    def config_extend(self, data):
        data['isns_servers'] = data['isns_servers'].split()
        return data

    @accepts(Dict(
        'iscsiglobal_update',
        Str('basename'),
        List('isns_servers', items=[Str('server')]),
        Int('pool_avail_threshold', validators=[Range(min=1, max=99)], null=True),
        Bool('alua'),
        update=True
    ))
    async def do_update(self, data):
        """
        `alua` is a no-op for FreeNAS.
        """
        old = await self.config()

        new = old.copy()
        new.update(data)

        verrors = ValidationErrors()

        servers = data.get('isns_servers') or []
        for server in servers:
            reg = RE_IP_PORT.search(server)
            if reg:
                ip = reg.group(1)
                if ip and ip[0] == '[' and ip[-1] == ']':
                    ip = ip[1:-1]
                try:
                    ip_validator = IpAddress()
                    ip_validator(ip)
                    continue
                except ValueError:
                    pass
            verrors.add('iscsiglobal_update.isns_servers', f'Server "{server}" is not a valid IP(:PORT)? tuple.')

        if verrors:
            raise verrors

        new['isns_servers'] = '\n'.join(servers)

        await self._update_service(old, new)

        if old['alua'] != new['alua']:
            await self.middleware.call('etc.generate', 'loader')

        return await self.config()

    @filterable
    def sessions(self, filters, options):
        """
        Get a list of currently running iSCSI sessions. This includes initiator and target names
        and the unique connection IDs.
        """
        def transform(tag, text):
            if tag in (
                'target_portal_group_tag', 'max_data_segment_length', 'max_burst_length',
                'first_burst_length',
            ) and text.isdigit():
                return int(text)
            if tag in ('immediate_data', 'iser'):
                return bool(int(text))
            if tag in ('header_digest', 'data_digest', 'offload') and text == 'None':
                return None
            return text

        cp = subprocess.run(['ctladm', 'islist', '-x'], capture_output=True, text=True)
        connections = etree.fromstring(cp.stdout)
        sessions = []
        for connection in connections.xpath("//connection"):
            sessions.append({
                i.tag: transform(i.tag, i.text) for i in connection.iterchildren()
            })
        return filter_list(sessions, filters, options)

    @private
    async def alua_enabled(self):
        """
        Returns whether iSCSI ALUA is enabled or not.
        """
        if await self.middleware.call('system.is_freenas'):
            return False
        if not await self.middleware.call('failover.licensed'):
            return False

        license = (await self.middleware.call('system.info'))['license']
        if license and 'FIBRECHANNEL' in license['features']:
            return True

        return (await self.middleware.call('iscsi.global.config'))['alua']

    @private
    async def terminate_luns_for_pool(self, pool_name):
        cp = await run(['ctladm', 'devlist', '-b', 'block', '-x'], check=False, encoding='utf8')
        for lun in etree.fromstring(cp.stdout).xpath('//lun'):
            lun_id = lun.attrib['id']

            try:
                path = lun.xpath('//file')[0].text
            except IndexError:
                continue

            if path.startswith(f'/dev/zvol/{pool_name}/'):
                self.logger.info('Terminating LUN %s (%s)', lun_id, path)
                await run(['ctladm', 'remove', '-b', 'block', '-l', lun_id], check=False)


class ISCSIPortalService(CRUDService):

    class Config:
        datastore = 'services.iscsitargetportal'
        datastore_extend = 'iscsi.portal.config_extend'
        datastore_prefix = 'iscsi_target_portal_'
        namespace = 'iscsi.portal'

    @private
    async def config_extend(self, data):
        data['listen'] = []
        for portalip in await self.middleware.call(
            'datastore.query',
            'services.iscsitargetportalip',
            [('portal', '=', data['id'])],
            {'prefix': 'iscsi_target_portalip_'}
        ):
            data['listen'].append({
                'ip': portalip['ip'],
                'port': portalip['port'],
            })
        data['discovery_authmethod'] = AUTHMETHOD_LEGACY_MAP.get(
            data.pop('discoveryauthmethod')
        )
        data['discovery_authgroup'] = data.pop('discoveryauthgroup')
        return data

    @accepts()
    async def listen_ip_choices(self):
        """
        Returns possible choices for `listen.ip` attribute of portal create and update.
        """
        choices = {'0.0.0.0': '0.0.0.0'}
        alua = (await self.middleware.call('iscsi.global.config'))['alua']
        if alua:
            # If ALUA is enabled we actually want to show the user the IPs of each node
            # instead of the VIP so its clear its not going to bind to the VIP even though
            # thats the value used under the hoods.
            for i in await self.middleware.call('datastore.query', 'network.Interfaces', [
                ('int_vip', 'nin', [None, '']),
            ]):
                choices[i['int_vip']] = f'{i["int_ipv4address"]}/{i["int_ipv4address_b"]}'

            for i in await self.middleware.call('datastore.query', 'network.Alias', [
                ('alias_vip', 'nin', [None, '']),
            ]):
                choices[i['alias_vip']] = f'{i["alias_v4address"]}/{i["alias_v4address_b"]}'

        else:
            for i in await self.middleware.call('interface.query'):
                for alias in i['aliases']:
                    choices[alias['address']] = alias['address']
        return choices

    async def __validate(self, verrors, data, schema, old=None):
        if not data['listen']:
            verrors.add(f'{schema}.listen', 'At least one listen entry is required.')
        else:
            system_ips = [
                ip['address'] for ip in await self.middleware.call('interface.ip_in_use')
            ]
            system_ips.extend(['0.0.0.0', '::'])
            new_ips = set(i['ip'] for i in data['listen']) - set(i['ip'] for i in old['listen']) if old else set()
            for i in data['listen']:
                filters = [
                    ('iscsi_target_portalip_ip', '=', i['ip']),
                    ('iscsi_target_portalip_port', '=', i['port']),
                ]
                if schema == 'iscsiportal_update':
                    filters.append(('iscsi_target_portalip_portal', '!=', data['id']))
                if await self.middleware.call(
                    'datastore.query', 'services.iscsitargetportalip', filters
                ):
                    verrors.add(f'{schema}.listen', f'{i["ip"]}:{i["port"]} already in use.')

                if (
                    (i['ip'] in new_ips or not new_ips) and
                    i['ip'] not in system_ips
                ):
                    verrors.add(f'{schema}.listen', f'IP {i["ip"]} not configured on this system.')

        if data['discovery_authgroup']:
            if not await self.middleware.call(
                'datastore.query', 'services.iscsitargetauthcredential',
                [('iscsi_target_auth_tag', '=', data['discovery_authgroup'])]
            ):
                verrors.add(
                    f'{schema}.discovery_authgroup',
                    f'Auth Group "{data["discovery_authgroup"]}" not found.',
                    errno.ENOENT,
                )
        elif data['discovery_authmethod'] in ('CHAP', 'CHAP_MUTUAL'):
            verrors.add(f'{schema}.discovery_authgroup', 'This field is required if discovery method is '
                                                         'set to CHAP or CHAP Mutual.')

    @accepts(Dict(
        'iscsiportal_create',
        Str('comment'),
        Str('discovery_authmethod', default='NONE', enum=['NONE', 'CHAP', 'CHAP_MUTUAL']),
        Int('discovery_authgroup', default=None, null=True),
        List('listen', required=True, items=[
            Dict(
                'listen',
                IPAddr('ip', required=True),
                Int('port', default=3260, validators=[Range(min=1, max=65535)]),
            ),
        ], default=[]),
        register=True,
    ))
    async def do_create(self, data):
        """
        Create a new iSCSI Portal.

        `discovery_authgroup` is required for CHAP and CHAP_MUTUAL.
        """
        verrors = ValidationErrors()
        await self.__validate(verrors, data, 'iscsiportal_create')
        if verrors:
            raise verrors

        # tag attribute increments sequentially
        data['tag'] = (await self.middleware.call(
            'datastore.query', self._config.datastore, [], {'count': True}
        )) + 1

        listen = data.pop('listen')
        data['discoveryauthgroup'] = data.pop('discovery_authgroup', None)
        data['discoveryauthmethod'] = AUTHMETHOD_LEGACY_MAP.inv.get(data.pop('discovery_authmethod'), 'None')
        pk = await self.middleware.call(
            'datastore.insert', self._config.datastore, data,
            {'prefix': self._config.datastore_prefix}
        )
        try:
            await self.__save_listen(pk, listen)
        except Exception as e:
            await self.middleware.call('datastore.delete', self._config.datastore, pk)
            raise e

        await self._service_change('iscsitarget', 'reload')

        return await self._get_instance(pk)

    async def __save_listen(self, pk, new, old=None):
        """
        Update database with a set new listen IP:PORT tuples.
        It will delete no longer existing addresses and add new ones.
        """
        new_listen_set = set([tuple(i.items()) for i in new])
        old_listen_set = set([tuple(i.items()) for i in old]) if old else set()
        for i in new_listen_set - old_listen_set:
            i = dict(i)
            await self.middleware.call(
                'datastore.insert',
                'services.iscsitargetportalip',
                {'portal': pk, 'ip': i['ip'], 'port': i['port']},
                {'prefix': 'iscsi_target_portalip_'}
            )

        for i in old_listen_set - new_listen_set:
            i = dict(i)
            portalip = await self.middleware.call(
                'datastore.query',
                'services.iscsitargetportalip',
                [('portal', '=', pk), ('ip', '=', i['ip']), ('port', '=', i['port'])],
                {'prefix': 'iscsi_target_portalip_'}
            )
            if portalip:
                await self.middleware.call(
                    'datastore.delete', 'services.iscsitargetportalip', portalip[0]['id']
                )

    @accepts(
        Int('id'),
        Patch(
            'iscsiportal_create',
            'iscsiportal_update',
            ('attr', {'update': True})
        )
    )
    async def do_update(self, pk, data):
        """
        Update iSCSI Portal `id`.
        """

        old = await self._get_instance(pk)

        new = old.copy()
        new.update(data)

        verrors = ValidationErrors()
        await self.__validate(verrors, new, 'iscsiportal_update', old)
        if verrors:
            raise verrors

        listen = new.pop('listen')
        new['discoveryauthgroup'] = new.pop('discovery_authgroup', None)
        new['discoveryauthmethod'] = AUTHMETHOD_LEGACY_MAP.inv.get(new.pop('discovery_authmethod'), 'None')

        await self.__save_listen(pk, listen, old['listen'])

        await self.middleware.call(
            'datastore.update', self._config.datastore, pk, new,
            {'prefix': self._config.datastore_prefix}
        )

        await self._service_change('iscsitarget', 'reload')

        return await self._get_instance(pk)

    @accepts(Int('id'))
    async def do_delete(self, id):
        """
        Delete iSCSI Portal `id`.
        """
        result = await self.middleware.call('datastore.delete', self._config.datastore, id)

        for i, portal in enumerate(await self.middleware.call('iscsi.portal.query', [], {'order_by': ['tag']})):
            await self.middleware.call(
                'datastore.update', self._config.datastore, portal['id'], {'tag': i + 1},
                {'prefix': self._config.datastore_prefix}
            )

        await self._service_change('iscsitarget', 'reload')

        return result


class iSCSITargetAuthCredentialService(CRUDService):

    class Config:
        namespace = 'iscsi.auth'
        datastore = 'services.iscsitargetauthcredential'
        datastore_prefix = 'iscsi_target_auth_'

    @accepts(Dict(
        'iscsi_auth_create',
        Int('tag', required=True),
        Str('user', required=True),
        Str('secret', required=True),
        Str('peeruser'),
        Str('peersecret'),
        register=True
    ))
    async def do_create(self, data):
        """
        Create an iSCSI Authorized Access.

        `tag` should be unique among all configured iSCSI Authorized Accesses.

        `secret` and `peersecret` should have length between 12-16 letters inclusive.

        `peeruser` and `peersecret` are provided only when configuring mutual CHAP. `peersecret` should not be
        similar to `secret`.
        """
        verrors = ValidationErrors()
        await self.validate(data, 'iscsi_auth_create', verrors)

        if verrors:
            raise verrors

        data['id'] = await self.middleware.call(
            'datastore.insert', self._config.datastore, data,
            {'prefix': self._config.datastore_prefix}
        )

        await self._service_change('iscsitarget', 'reload')

        return await self._get_instance(data['id'])

    @accepts(
        Int('id'),
        Patch(
            'iscsi_auth_create',
            'iscsi_auth_update',
            ('attr', {'update': True})
        )
    )
    async def do_update(self, id, data):
        """
        Update iSCSI Authorized Access of `id`.
        """
        old = await self._get_instance(id)

        new = old.copy()
        new.update(data)

        verrors = ValidationErrors()
        await self.validate(new, 'iscsi_auth_update', verrors, old)

        if verrors:
            raise verrors

        await self.middleware.call(
            'datastore.update', self._config.datastore, id, new,
            {'prefix': self._config.datastore_prefix}
        )

        await self._service_change('iscsitarget', 'reload')

        return await self._get_instance(id)

    @accepts(Int('id'))
    async def do_delete(self, id):
        """
        Delete iSCSI Authorized Access of `id`.
        """
        return await self.middleware.call(
            'datastore.delete', self._config.datastore, id
        )

    @private
    async def validate(self, data, schema_name, verrors, old=None):

        filters = [('tag', '=', data['tag'])]
        if old:
            filters.append(('id', '!=', old['id']))

        if await self.middleware.call('iscsi.auth.query', filters):
            verrors.add(f'{schema_name}.tag', f'Tag {data["tag"]!r} is already in use.')

        secret = data.get('secret')
        peer_secret = data.get('peersecret')
        peer_user = data.get('peeruser', '')

        if not peer_user and peer_secret:
            verrors.add(
                f'{schema_name}.peersecret',
                'The peer user is required if you set a peer secret.'
            )

        if len(secret) < 12 or len(secret) > 16:
            verrors.add(
                f'{schema_name}.secret',
                'Secret must be between 12 and 16 characters.'
            )

        if not peer_user:
            return

        if not peer_secret:
            verrors.add(
                f'{schema_name}.peersecret',
                'The peer secret is required if you set a peer user.'
            )
        elif peer_secret == secret:
            verrors.add(
                f'{schema_name}.peersecret',
                'The peer secret cannot be the same as user secret.'
            )
        elif peer_secret:
            if len(peer_secret) < 12 or len(peer_secret) > 16:
                verrors.add(
                    f'{schema_name}.peersecret',
                    'Peer Secret must be between 12 and 16 characters.'
                )


class iSCSITargetExtentService(CRUDService):

    class Config:
        namespace = 'iscsi.extent'
        datastore = 'services.iscsitargetextent'
        datastore_prefix = 'iscsi_target_extent_'
        datastore_extend = 'iscsi.extent.extend'

    @accepts(Dict(
        'iscsi_extent_create',
        Str('name', required=True),
        Str('type', enum=['DISK', 'FILE'], default='DISK'),
        Str('disk', default=None, null=True),
        Str('serial', default=None, null=True),
        Str('path', default=None, null=True),
        Int('filesize', default=0),
        Int('blocksize', enum=[512, 1024, 2048, 4096], default=512),
        Bool('pblocksize'),
        Int('avail_threshold', validators=[Range(min=1, max=99)], null=True),
        Str('comment'),
        Bool('insecure_tpc', default=True),
        Bool('xen'),
        Str('rpm', enum=['UNKNOWN', 'SSD', '5400', '7200', '10000', '15000'],
            default='SSD'),
        Bool('ro'),
        Bool('enabled', default=True),
        register=True
    ))
    async def do_create(self, data):
        """
        Create an iSCSI Extent.

        When `type` is set to FILE, attribute `filesize` is used and it represents number of bytes. `filesize` if
        not zero should be a multiple of `blocksize`. `path` is a required attribute with `type` set as FILE and it
        should be ensured that it does not come under a jail root.

        With `type` being set to DISK, a valid ZVOL or DISK should be provided.

        `insecure_tpc` when enabled allows an initiator to bypass normal access control and access any scannable
        target. This allows xcopy operations otherwise blocked by access control.

        `xen` is a boolean value which is set to true if Xen is being used as the iSCSI initiator.

        `ro` when set to true prevents the initiator from writing to this LUN.
        """
        verrors = ValidationErrors()
        await self.compress(data)
        await self.validate(data)
        await self.clean(data, 'iscsi_extent_create', verrors)

        if verrors:
            raise verrors

        await self.save(data, 'iscsi_extent_create', verrors)

        data['id'] = await self.middleware.call(
            'datastore.insert', self._config.datastore, data,
            {'prefix': self._config.datastore_prefix}
        )

        return await self._get_instance(data['id'])

    @accepts(
        Int('id'),
        Patch(
            'iscsi_extent_create',
            'iscsi_extent_update',
            ('attr', {'update': True})
        )
    )
    async def do_update(self, id, data):
        """
        Update iSCSI Extent of `id`.
        """
        verrors = ValidationErrors()
        old = await self._get_instance(id)

        new = old.copy()
        new.update(data)

        await self.compress(new)
        await self.validate(new)
        await self.clean(
            new, 'iscsi_extent_update', verrors, old=old
        )

        if verrors:
            raise verrors

        await self.save(new, 'iscsi_extent_update', verrors)

        await self.middleware.call(
            'datastore.update',
            self._config.datastore,
            id,
            new,
            {'prefix': self._config.datastore_prefix}
        )

        return await self._get_instance(id)

    @accepts(
        Int('id'),
        Bool('remove', default=False),
    )
    async def do_delete(self, id, remove):
        """
        Delete iSCSI Extent of `id`.

        If `id` iSCSI Extent's `type` was configured to FILE, `remove` can be set to remove the configured file.
        """
        data = await self._get_instance(id)

        if remove:
            await self.compress(data)
            delete = await self.remove_extent_file(data)

            if delete is not True:
                raise CallError('Failed to remove extent file')

        for target_to_extent in await self.middleware.call('iscsi.targetextent.query', [['extent', '=', id]]):
            await self.middleware.call('iscsi.targetextent.delete', target_to_extent['id'])

        return await self.middleware.call(
            'datastore.delete', self._config.datastore, id
        )

    @private
    async def validate(self, data):
        data['serial'] = await self.extent_serial(data['serial'])
        data['naa'] = self.extent_naa(data.get('naa'))

    @private
    async def compress(self, data):
        extent_type = data['type']
        extent_rpm = data['rpm']

        if extent_type == 'DISK':
            extent_disk = data['disk']

            if extent_disk.startswith('zvol'):
                data['type'] = 'ZVOL'
            elif extent_disk.startswith('hast'):
                data['type'] = 'HAST'
            else:
                data['type'] = 'Disk'
        elif extent_type == 'FILE':
            data['type'] = 'File'

        if extent_rpm == 'UNKNOWN':
            data['rpm'] = 'Unknown'

        return data

    @private
    async def extend(self, data):
        extent_type = data['type'].upper()
        extent_rpm = data['rpm'].upper()

        if extent_type != 'FILE':
            # ZVOL and HAST are type DISK
            extent_type = 'DISK'
            # If extent is set to a disk ( not ZVOL and HAST ) - let's reflect this in the output

            disk = await self.middleware.call('disk.query', [['identifier', '=', data['path']]])
            if disk:
                data['disk'] = disk[0]['name']
        else:
            extent_size = data['filesize']

            # Legacy Compat for having 2[KB, MB, GB, etc] in database
            if not str(extent_size).isdigit():
                suffixes = {
                    'PB': 1125899906842624,
                    'TB': 1099511627776,
                    'GB': 1073741824,
                    'MB': 1048576,
                    'KB': 1024,
                    'B': 1
                }
                for x in suffixes.keys():
                    if str(extent_size).upper().endswith(x):
                        extent_size = str(extent_size).upper().strip(x)
                        extent_size = int(extent_size) * suffixes[x]

                        data['filesize'] = extent_size

        data['rpm'] = extent_rpm
        data['type'] = extent_type

        return data

    @private
    async def clean(self, data, schema_name, verrors, old=None):
        await self.clean_name(data, schema_name, verrors, old=old)
        await self.clean_type_and_path(data, schema_name, verrors)
        await self.clean_size(data, schema_name, verrors)

    @private
    async def clean_name(self, data, schema_name, verrors, old=None):
        name = data['name']
        old = old['name'] if old is not None else None
        serial = data['serial']
        name_filters = [('name', '=', name)]

        if '"' in name:
            verrors.add(f'{schema_name}.name', 'Double quotes are not allowed')

        if '"' in serial:
            verrors.add(f'{schema_name}.serial', 'Double quotes are not allowed')

        if name != old or old is None:
            name_result = await self.middleware.call(
                'datastore.query', self._config.datastore,
                name_filters,
                {'prefix': self._config.datastore_prefix})

            if name_result:
                verrors.add(f'{schema_name}.name',
                            'Extent name must be unique')

    @private
    async def clean_type_and_path(self, data, schema_name, verrors):
        extent_type = data['type']
        disk = data['disk']
        path = data['path']

        if extent_type is None:
            return data

        if extent_type == 'Disk':
            if not disk:
                verrors.add(f'{schema_name}.disk', 'This field is required')
        elif extent_type == 'ZVOL':
            if disk.startswith('zvol') and not os.path.exists(f'/dev/{disk}'):
                verrors.add(f'{schema_name}.disk',
                            f'ZVOL {disk} does not exist')
        elif extent_type == 'File':
            if not path:
                verrors.add(f'{schema_name}.path', 'This field is required')
                raise verrors  # They need this for anything else

            if '/iocage' in path:
                    verrors.add(
                        f'{schema_name}.path',
                        'You need to specify a filepath outside of a jail root'
                    )

            if (os.path.exists(path) and not
                    os.path.isfile(path)) or path[-1] == '/':
                verrors.add(f'{schema_name}.path',
                            'You need to specify a filepath not a directory')

            await check_path_resides_within_volume(
                verrors, self.middleware, f'{schema_name}.path', path
            )

        return data

    @private
    async def clean_size(self, data, schema_name, verrors):
        extent_type = data['type']
        path = data['path']
        size = data['filesize']
        blocksize = data['blocksize']

        if extent_type != 'FILE':
            return data

        if (
            size == 0 and path and (not os.path.exists(path) or (
                os.path.exists(path) and not
                os.path.isfile(path)
            ))
        ):
            verrors.add(
                f'{schema_name}.path',
                'The file must exist if the extent size is set to auto (0)')
        elif extent_type == 'FILE' and not path:
            verrors.add(f'{schema_name}.path', 'This field is required')

        if size and size != 0 and blocksize:
            if float(size) % blocksize:
                verrors.add(f'{schema_name}.filesize',
                            'File size must be a multiple of block size')

        return data

    @private
    async def extent_serial(self, serial):
        # TODO Just ported, let's do something different later? - Brandon
        if serial is None:
            try:
                nic = (await self.middleware.call('interface.query',
                                                  [['name', 'rnin', 'vlan'],
                                                   ['name', 'rnin', 'lagg'],
                                                   ['name', 'rnin', 'epair'],
                                                   ['name', 'rnin', 'vnet'],
                                                   ['name', 'rnin', 'bridge']])
                       )[0]
                mac = nic['link_address'].replace(':', '')

                ltg = await self.query()
                if len(ltg) > 0:
                    lid = ltg[0]['id']
                else:
                    lid = 0
                return f'{mac.strip()}{lid:02}'
            except Exception:
                return '10000001'
        else:
            return serial

    @private
    def extent_naa(self, naa):
        if naa is None:
            return '0x6589cfc000000' + hashlib.sha256(str(uuid.uuid4()).encode()).hexdigest()[0:19]
        else:
            return naa

    @accepts(List('exclude', default=[]))
    async def disk_choices(self, exclude):
        """
        Exclude will exclude the path from being in the used_zvols list,
        allowing the user to keep the same item on update
        """
        diskchoices = {}

        zvol_query_filters = [('type', '=', 'ZVOL')]
        for e in exclude:
            if e:
                zvol_query_filters.append(('path', '!=', e))

        zvol_query = await self.query(zvol_query_filters)

        used_zvols = [i['path'] for i in zvol_query]

        zfs_snaps = await self.middleware.call(
            'zfs.snapshot.query', [], {'select': ['name'], 'order_by': ['name']}
        )

        zvols = await self.middleware.call(
            'pool.dataset.query',
            [('type', '=', 'VOLUME')]
        )

        zvol_list = [ds['name'] for ds in zvols]

        for zvol in zvols:
            zvol_name = zvol['name']
            zvol_size = zvol['volsize']['value']
            if f'zvol/{zvol_name}' not in used_zvols:
                diskchoices[f'zvol/{zvol_name}'] = f'{zvol_name} ({zvol_size})'

        for snap in zfs_snaps:
            ds_name, snap_name = snap['name'].rsplit('@', 1)
            if ds_name in zvol_list:
                diskchoices[f'zvol/{snap["name"]}'] = f'{snap["name"]} [ro]'

        for disk in await self.middleware.call('disk.get_unused'):
            size = await self.middleware.call('notifier.humanize_size', disk['size'])
            diskchoices[disk['name']] = f'{disk["name"]} ({size})'

        return diskchoices

    @private
    async def save(self, data, schema_name, verrors):

        extent_type = data['type']
        disk = data.pop('disk', None)

        if extent_type == 'File':
            path = data['path']
            dirs = '/'.join(path.split('/')[:-1])

            if not os.path.exists(dirs):
                try:
                    os.makedirs(dirs)
                except Exception as e:
                    self.logger.error(
                        f'Unable to create dirs for extent file: {e}')

            if not os.path.exists(path):
                extent_size = data['filesize']

                await run(['truncate', '-s', str(extent_size), path])

            await self._service_change('iscsitarget', 'reload')
        else:
            data['path'] = disk

            if disk.startswith('multipath'):
                await self.middleware.call('disk.unlabel', disk)
                await self.middleware.call('disk.label', disk, f'extent_{disk}')
            elif not disk.startswith('hast') and not disk.startswith('zvol'):
                disk_filters = [('name', '=', disk), ('expiretime', '=', None)]
                try:
                    disk_object = (await self.middleware.call('disk.query',
                                                              disk_filters))[0]
                    disk_identifier = disk_object.get('identifier', None)
                    data['path'] = disk_identifier

                    if disk_identifier.startswith('{devicename}') or disk_identifier.startswith(
                        '{uuid}'
                    ):
                        try:
                            await self.middleware.call('disk.label', disk, f'extent_{disk}')
                        except Exception as e:
                            verrors.add(
                                f'{schema_name}.disk',
                                f'Serial not found and glabel failed for {disk}: {str(e)}'
                            )

                            if verrors:
                                raise verrors
                        await self.middleware.call(
                            'disk.sync', disk.replace('/dev/', '')
                        )
                except IndexError:
                    # It's not a disk, but a ZVOL
                    pass

    @private
    async def remove_extent_file(self, data):
        if data['type'] == 'File':
            try:
                os.unlink(data['path'])
            except Exception as e:
                return e

        return True


class iSCSITargetAuthorizedInitiator(CRUDService):

    class Config:
        namespace = 'iscsi.initiator'
        datastore = 'services.iscsitargetauthorizedinitiator'
        datastore_prefix = 'iscsi_target_initiator_'
        datastore_extend = 'iscsi.initiator.extend'

    @accepts(Dict(
        'iscsi_initiator_create',
        Int('tag', default=0),
        List('initiators', default=[]),
        List('auth_network', items=[IPAddr('ip', network=True)], default=[]),
        Str('comment'),
        register=True
    ))
    async def do_create(self, data):
        """
        Create an iSCSI Initiator.

        `initiators` is a list of initiator hostnames which are authorized to access an iSCSI Target. To allow all
        possible initiators, `initiators` can be left empty.

        `auth_network` is a list of IP/CIDR addresses which are allowed to use this initiator. If all networks are
        to be allowed, this field should be left empty.
        """
        if data['tag'] == 0:
            i = len((await self.query())) + 1
            while True:
                tag_result = await self.query([('tag', '=', i)])
                if not tag_result:
                    break
                i += 1
            data['tag'] = i

        await self.compress(data)

        data['id'] = await self.middleware.call(
            'datastore.insert', self._config.datastore, data,
            {'prefix': self._config.datastore_prefix})

        await self._service_change('iscsitarget', 'reload')

        return await self._get_instance(data['id'])

    @accepts(
        Int('id'),
        Patch(
            'iscsi_initiator_create',
            'iscsi_initiator_update',
            ('attr', {'update': True})
        )
    )
    async def do_update(self, id, data):
        """
        Update iSCSI initiator of `id`.
        """
        old = await self._get_instance(id)

        new = old.copy()
        new.update(data)

        await self.compress(new)
        await self.middleware.call(
            'datastore.update', self._config.datastore, id, new,
            {'prefix': self._config.datastore_prefix})

        await self._service_change('iscsitarget', 'reload')

        return await self._get_instance(id)

    @accepts(Int('id'))
    async def do_delete(self, id):
        """
        Delete iSCSI initiator of `id`.
        """
        result = await self.middleware.call(
            'datastore.delete', self._config.datastore, id
        )

        for i, initiator in enumerate(await self.middleware.call('iscsi.initiator.query', [], {'order_by': ['tag']})):
            await self.middleware.call(
                'datastore.update', self._config.datastore, initiator['id'], {'tag': i + 1},
                {'prefix': self._config.datastore_prefix}
            )

        await self._service_change('iscsitarget', 'reload')

        return result

    @private
    async def compress(self, data):
        initiators = data['initiators']
        auth_network = data['auth_network']

        initiators = 'ALL' if not initiators else '\n'.join(initiators)
        auth_network = 'ALL' if not auth_network else '\n'.join(auth_network)

        data['initiators'] = initiators
        data['auth_network'] = auth_network

        return data

    @private
    async def extend(self, data):
        initiators = data['initiators']
        auth_network = data['auth_network']

        initiators = [] if initiators == 'ALL' else initiators.split()
        auth_network = [] if auth_network == 'ALL' else auth_network.split()

        data['initiators'] = initiators
        data['auth_network'] = auth_network

        return data


class iSCSITargetService(CRUDService):

    class Config:
        namespace = 'iscsi.target'
        datastore = 'services.iscsitarget'
        datastore_prefix = 'iscsi_target_'
        datastore_extend = 'iscsi.target.extend'

    @private
    async def extend(self, data):
        data['mode'] = data['mode'].upper()
        data['groups'] = await self.middleware.call(
            'datastore.query',
            'services.iscsitargetgroups',
            [('iscsi_target', '=', data['id'])],
        )
        for group in data['groups']:
            group.pop('id')
            group.pop('iscsi_target')
            group.pop('iscsi_target_initialdigest')
            for i in ('portal', 'initiator'):
                val = group.pop(f'iscsi_target_{i}group')
                if val:
                    val = val['id']
                group[i] = val
            group['auth'] = group.pop('iscsi_target_authgroup')
            group['authmethod'] = AUTHMETHOD_LEGACY_MAP.get(
                group.pop('iscsi_target_authtype')
            )
        return data

    @accepts(Dict(
        'iscsi_target_create',
        Str('name', required=True),
        Str('alias', null=True),
        Str('mode', enum=['ISCSI', 'FC', 'BOTH'], default='ISCSI'),
        List('groups', default=[], items=[
            Dict(
                'group',
                Int('portal', required=True),
                Int('initiator', default=None, null=True),
                Str('authmethod', enum=['NONE', 'CHAP', 'CHAP_MUTUAL'], default='NONE'),
                Int('auth', default=None, null=True),
            ),
        ]),
        register=True
    ))
    async def do_create(self, data):
        """
        Create an iSCSI Target.

        `groups` is a list of group dictionaries which provide information related to using a `portal`, `initiator`,
        `authmethod` and `auth` with this target. `auth` represents a valid iSCSI Authorized Access and defaults to
        null.
        """
        verrors = ValidationErrors()
        await self.__validate(verrors, data, 'iscsi_target_create')
        if verrors:
            raise verrors

        await self.compress(data)
        groups = data.pop('groups')
        pk = await self.middleware.call(
            'datastore.insert', self._config.datastore, data,
            {'prefix': self._config.datastore_prefix})
        try:
            await self.__save_groups(pk, groups)
        except Exception as e:
            await self.middleware.call('datastore.delete', self._config.datastore, pk)
            raise e

        await self._service_change('iscsitarget', 'reload')

        return await self._get_instance(pk)

    async def __save_groups(self, pk, new, old=None):
        """
        Update database with a set of new target groups.
        It will delete no longer existing groups and add new ones.
        """
        new_set = set([tuple(i.items()) for i in new])
        old_set = set([tuple(i.items()) for i in old]) if old else set()

        for i in old_set - new_set:
            i = dict(i)
            targetgroup = await self.middleware.call(
                'datastore.query',
                'services.iscsitargetgroups',
                [
                    ('iscsi_target', '=', pk),
                    ('iscsi_target_portalgroup', '=', i['portal']),
                    ('iscsi_target_initiatorgroup', '=', i['initiator']),
                    ('iscsi_target_authtype', '=', i['authmethod']),
                    ('iscsi_target_authgroup', '=', i['auth']),
                ],
            )
            if targetgroup:
                await self.middleware.call(
                    'datastore.delete', 'services.iscsitargetgroups', targetgroup[0]['id']
                )

        for i in new_set - old_set:
            i = dict(i)
            await self.middleware.call(
                'datastore.insert',
                'services.iscsitargetgroups',
                {
                    'iscsi_target': pk,
                    'iscsi_target_portalgroup': i['portal'],
                    'iscsi_target_initiatorgroup': i['initiator'],
                    'iscsi_target_authtype': i['authmethod'],
                    'iscsi_target_authgroup': i['auth'],
                },
            )

    async def __validate(self, verrors, data, schema_name, old=None):

        if not RE_TARGET_NAME.search(data['name']):
            verrors.add(
                f'{schema_name}.name',
                'Lowercase alphanumeric characters plus dot (.), dash (-), and colon (:) are allowed.'
            )
        else:
            filters = [('name', '=', data['name'])]
            if old:
                filters.append(('id', '!=', old['id']))
            names = await self.middleware.call(f'{self._config.namespace}.query', filters)
            if names:
                verrors.add(f'{schema_name}.name', 'Target name already exists')

        if data.get('alias') is not None:
            if '"' in data['alias']:
                verrors.add(f'{schema_name}.alias', 'Double quotes are not allowed')
            elif data['alias'] == 'target':
                verrors.add(f'{schema_name}.alias', 'target is a reserved word')
            else:
                filters = [('alias', '=', data['alias'])]
                if old:
                    filters.append(('id', '!=', old['id']))
                aliases = await self.middleware.call(f'{self._config.namespace}.query', filters)
                if aliases:
                    verrors.add(f'{schema_name}.alias', 'Alias already exists')

        if (
            data['mode'] != 'ISCSI' and
            not await self.middleware.call('system.feature_enabled', 'FIBRECHANNEL')
        ):
            verrors.add(f'{schema_name}.mode', 'Fibre Channel not enabled')

        # Creating target without groups should be allowed for API 1.0 compat
        # if not data['groups']:
        #    verrors.add(f'{schema_name}.groups', 'At least one group is required')

        db_portals = list(
            map(
                lambda v: v['id'],
                await self.middleware.call('datastore.query', 'services.iSCSITargetPortal', [
                    ['id', 'in', list(map(lambda v: v['portal'], data['groups']))]
                ])
            )
        )

        db_initiators = list(
            map(
                lambda v: v['id'],
                await self.middleware.call('datastore.query', 'services.iSCSITargetAuthorizedInitiator', [
                    ['id', 'in', list(map(lambda v: v['initiator'], data['groups']))]
                ])
            )
        )

        portals = []
        for i, group in enumerate(data['groups']):
            if group['portal'] in portals:
                verrors.add(f'{schema_name}.groups.{i}.portal', f'Portal {group["portal"]} cannot be '
                                                                'duplicated on a target')
            elif group['portal'] not in db_portals:
                verrors.add(
                    f'{schema_name}.groups.{i}.portal',
                    f'{group["portal"]} Portal not found in database'
                )
            else:
                portals.append(group['portal'])

            if group['initiator'] and group['initiator'] not in db_initiators:
                verrors.add(
                    f'{schema_name}.groups.{i}.initiator',
                    f'{group["initiator"]} Initiator not found in database'
                )

            if not group['auth'] and group['authmethod'] in ('CHAP', 'CHAP_MUTUAL'):
                verrors.add(f'{schema_name}.groups.{i}.auth', 'Authentication group is required for '
                                                              'CHAP and CHAP Mutual')
            elif group['auth'] and group['authmethod'] == 'CHAP_MUTUAL':
                auth = await self.middleware.call('iscsi.auth.query', [('id', '=', group['auth'])])
                if not auth:
                    verrors.add(f'{schema_name}.groups.{i}.auth', 'Authentication group not found', errno.ENOENT)
                else:
                    if not auth[0]['peeruser']:
                        verrors.add(f'{schema_name}.groups.{i}.auth', f'Authentication group {group["auth"]} '
                                                                      'does not support CHAP Mutual')

    @accepts(
        Int('id'),
        Patch(
            'iscsi_target_create',
            'iscsi_target_update',
            ('attr', {'update': True})
        )
    )
    async def do_update(self, id, data):
        """
        Update iSCSI Target of `id`.
        """
        old = await self._get_instance(id)
        new = old.copy()
        new.update(data)

        verrors = ValidationErrors()
        await self.__validate(verrors, new, 'iscsi_target_create', old=old)
        if verrors:
            raise verrors

        await self.compress(new)
        groups = new.pop('groups')

        oldgroups = old.copy()
        await self.compress(oldgroups)
        oldgroups = oldgroups['groups']

        await self.middleware.call(
            'datastore.update', self._config.datastore, id, new,
            {'prefix': self._config.datastore_prefix}
        )

        await self.__save_groups(id, groups, oldgroups)

        await self._service_change('iscsitarget', 'reload')

        return await self._get_instance(id)

    @accepts(Int('id'))
    async def do_delete(self, id):
        """
        Delete iSCSI Target of `id`.

        Deleting an iSCSI Target makes sure we delete all Associated Targets which use `id` iSCSI Target.
        """
        for target_to_extent in await self.middleware.call('iscsi.targetextent.query', [['target', '=', id]]):
            await self.middleware.call('iscsi.targetextent.delete', target_to_extent['id'])

        rv = await self.middleware.call('datastore.delete', self._config.datastore, id)
        await self._service_change('iscsitarget', 'reload')
        return rv

    @private
    async def compress(self, data):
        data['mode'] = data['mode'].lower()
        for group in data['groups']:
            group['authmethod'] = AUTHMETHOD_LEGACY_MAP.inv.get(group.pop('authmethod'), 'NONE')
        return data


class iSCSITargetToExtentService(CRUDService):

    class Config:
        namespace = 'iscsi.targetextent'
        datastore = 'services.iscsitargettoextent'
        datastore_prefix = 'iscsi_'
        datastore_extend = 'iscsi.targetextent.extend'

    @accepts(Dict(
        'iscsi_targetextent_create',
        Int('target', required=True),
        Int('lunid'),
        Int('extent', required=True),
        register=True
    ))
    async def do_create(self, data):
        """
        Create an Associated Target.

        `lunid` will be automatically assigned if it is not provided based on the `target`.
        """
        verrors = ValidationErrors()

        await self.validate(data, 'iscsi_targetextent_create', verrors)

        if verrors:
            raise verrors

        data['id'] = await self.middleware.call(
            'datastore.insert', self._config.datastore, data,
            {'prefix': self._config.datastore_prefix}
        )

        await self._service_change('iscsitarget', 'reload')

        return await self._get_instance(data['id'])

    @accepts(
        Int('id'),
        Patch(
            'iscsi_targetextent_create',
            'iscsi_targetextent_update',
            ('attr', {'update': True})
        )
    )
    async def do_update(self, id, data):
        """
        Update Associated Target of `id`.
        """
        verrors = ValidationErrors()
        old = await self._get_instance(id)

        new = old.copy()
        new.update(data)

        await self.validate(new, 'iscsi_targetextent_update', verrors, old)

        if verrors:
            raise verrors

        await self.middleware.call(
            'datastore.update', self._config.datastore, id, new,
            {'prefix': self._config.datastore_prefix})

        await self._service_change('iscsitarget', 'reload')

        return await self._get_instance(id)

    @accepts(Int('id'))
    async def do_delete(self, id):
        """
        Delete Associated Target of `id`.
        """
        result = await self.middleware.call(
            'datastore.delete', self._config.datastore, id
        )

        await self._service_change('iscsitarget', 'reload')

        return result

    @private
    async def extend(self, data):
        data['target'] = data['target']['id']
        data['extent'] = data['extent']['id']

        return data

    @private
    async def validate(self, data, schema_name, verrors, old=None):
        if old is None:
            old = {}

        old_lunid = old.get('lunid')
        target = data['target']
        old_target = old.get('target')
        extent = data['extent']
        if 'lunid' not in data:
            lunids = [
                o['lunid'] for o in await self.query(
                    [('target', '=', target)], {'order_by': ['lunid']}
                )
            ]
            if not lunids:
                lunid = 0
            else:
                diff = sorted(set(range(0, lunids[-1] + 1)).difference(lunids))
                lunid = diff[0] if diff else max(lunids) + 1

            data['lunid'] = lunid
        else:
            lunid = data['lunid']

        lun_map_size = sysctl.filter('kern.cam.ctl.lun_map_size')[0].value

        if lunid < 0 or lunid > lun_map_size - 1:
            verrors.add(
                f'{schema_name}.lunid',
                f'LUN ID must be a positive integer and lower than {lun_map_size - 1}'
            )

        if old_lunid != lunid and await self.query([
            ('lunid', '=', lunid), ('target', '=', target)
        ]):
            verrors.add(
                f'{schema_name}.lunid',
                'LUN ID is already being used for this target.'
            )

        if old_target != target and await self.query([
            ('target', '=', target), ('extent', '=', extent)]
        ):
            verrors.add(
                f'{schema_name}.target',
                'Extent is already in this target.'
            )


class ISCSIFSAttachmentDelegate(FSAttachmentDelegate):
    name = 'iscsi'
    title = 'iSCSI Extent'
    service = 'iscsitarget'

    async def query(self, path, enabled):
        results = []
        for extent in await self.middleware.call('iscsi.extent.query', [['type', '=', 'DISK'],
                                                                        ['enabled', '=', enabled]]):
            if is_child(extent['path'], os.path.join('zvol', os.path.relpath(path, '/mnt'))):
                results.append(extent)

        return results

    async def get_attachment_name(self, attachment):
        return attachment['name']

    async def delete(self, attachments):
        lun_ids = []
        for attachment in attachments:
            for te in await self.middleware.call('iscsi.targetextent.query', [['extent', '=', attachment['id']]]):
                await self.middleware.call('datastore.delete', 'services.iscsitargettoextent', te['id'])
                lun_ids.append(te['lunid'])

            await self.middleware.call('datastore.delete', 'services.iscsitargetextent', attachment['id'])

        await self._service_change('iscsitarget', 'reload')

        for lun_id in lun_ids:
            await run(['ctladm', 'remove', '-b', 'block', '-l', str(lun_id)], check=False)

    async def toggle(self, attachments, enabled):
        lun_ids = []
        for attachment in attachments:
            for te in await self.middleware.call('iscsi.targetextent.query', [['extent', '=', attachment['id']]]):
                await self.middleware.call('datastore.delete', 'services.iscsitargettoextent', te['id'])
                lun_ids.append(te['lunid'])

            await self.middleware.call('datastore.update', 'services.iscsitargetextent', attachment['id'],
                                       {'iscsi_target_extent_enabled': enabled})

        await self._service_change('iscsitarget', 'reload')

        if not enabled:
            for lun_id in lun_ids:
                await run(['ctladm', 'remove', '-b', 'block', '-l', str(lun_id)], check=False)


async def setup(middleware):
    await middleware.call('pool.dataset.register_attachment_delegate', ISCSIFSAttachmentDelegate(middleware))
