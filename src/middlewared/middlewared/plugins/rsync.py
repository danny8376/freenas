# Copyright 2017 iXsystems, Inc.
# All rights reserved
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted providing that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR
# IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
# OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
# STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING
# IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
#####################################################################

import asyncio
import asyncssh
import glob
import os
import re
import shlex

from middlewared.async_validators import check_path_resides_within_volume
from middlewared.schema import accepts, Bool, Cron, Dict, Str, Int, List, Patch
from middlewared.validators import Range, Match
from middlewared.service import (
    CallError, CRUDService, SystemServiceService, ValidationErrors,
    job, item_method, private,
)
from middlewared.utils import run_command_with_user_context


class RsyncdService(SystemServiceService):

    class Config:
        service = "rsync"
        service_model = 'rsyncd'
        datastore_prefix = "rsyncd_"

    @accepts(Dict(
        'rsyncd_update',
        Int('port', validators=[Range(min=1, max=65535)]),
        Str('auxiliary'),
        update=True
    ))
    async def do_update(self, data):
        """
        Update Rsyncd Service Configuration.

        `auxiliary` attribute can be used to pass on any additional parameters from rsyncd.conf(5).
        """
        old = await self.config()

        new = old.copy()
        new.update(data)

        await self._update_service(old, new)

        return new


class RsyncModService(CRUDService):

    class Config:
        datastore = 'services.rsyncmod'
        datastore_prefix = 'rsyncmod_'
        datastore_extend = 'rsyncmod.rsync_mod_extend'

    @private
    async def rsync_mod_extend(self, data):
        data['hostsallow'] = data['hostsallow'].split()
        data['hostsdeny'] = data['hostsdeny'].split()
        data['mode'] = data['mode'].upper()
        return data

    @private
    async def common_validation(self, data, schema_name):
        verrors = ValidationErrors()

        await check_path_resides_within_volume(verrors, self.middleware, f'{schema_name}.path', data.get('path'))

        for entity in ('user', 'group'):
            value = data.get(entity)
            if value not in map(
                    lambda e: e[entity if entity == 'group' else 'username'],
                    await self.middleware.call(f'{entity}.query')
            ):
                verrors.add(
                    f'{schema_name}.{entity}',
                    f'Please specify a valid {entity}'
                )

        verrors.check()

        data['hostsallow'] = ' '.join(data['hostsallow'])
        data['hostsdeny'] = ' '.join(data['hostsdeny'])
        data['mode'] = data['mode'].lower()

        return data

    @accepts(Dict(
        'rsyncmod_create',
        Str('name', validators=[Match(r'[^/\]]')]),
        Str('comment'),
        Str('path', required=True),
        Str('mode', enum=['RO', 'RW', 'WO']),
        Int('maxconn'),
        Str('user', default='nobody'),
        Str('group', default='nobody'),
        List('hostsallow', items=[Str('hostsallow')], default=[]),
        List('hostsdeny', items=[Str('hostdeny')], default=[]),
        Str('auxiliary'),
        register=True,
    ))
    async def do_create(self, data):
        """
        Create a Rsyncmod module.

        `path` represents the path to pool/dataset.

        `maxconn` is an integer value representing the maximum number of simultaneous connections. Zero represents
        unlimited.

        `hostsallow` is a list of patterns to match hostname/ip address of a connecting client. If list is empty,
        all hosts are allowed.

        `hostsdeny` is a list of patterns to match hostname/ip address of a connecting client. If the pattern is
        matched, access is denied to the client. If no client should be denied, this should be left empty.

        `auxiliary` attribute can be used to pass on any additional parameters from rsyncd.conf(5).
        """

        data = await self.common_validation(data, 'rsyncmod_create')

        data['id'] = await self.middleware.call(
            'datastore.insert',
            self._config.datastore,
            data,
            {'prefix': self._config.datastore_prefix}
        )

        await self._service_change('rsync', 'reload')

        return await self._get_instance(data['id'])

    @accepts(Int('id'), Patch('rsyncmod_create', 'rsyncmod_update', ('attr', {'update': True})))
    async def do_update(self, id, data):
        """
        Update Rsyncmod module of `id`.
        """
        module = await self._get_instance(id)
        module.update(data)

        module = await self.common_validation(module, 'rsyncmod_update')

        await self.middleware.call(
            'datastore.update',
            self._config.datastore,
            id,
            module,
            {'prefix': self._config.datastore_prefix}
        )

        await self._service_change('rsync', 'reload')

        return await self._get_instance(id)

    @accepts(Int('id'))
    async def do_delete(self, id):
        """
        Delete Rsyncmod module of `id`.
        """
        return await self.middleware.call('datastore.delete', self._config.datastore, id)


class RsyncTaskService(CRUDService):

    class Config:
        datastore = 'tasks.rsync'
        datastore_prefix = 'rsync_'
        datastore_extend = 'rsynctask.rsync_task_extend'

    @private
    async def rsync_task_extend(self, data):
        data['extra'] = list(filter(None, re.split(r"\s+", data["extra"])))
        for field in ('mode', 'direction'):
            data[field] = data[field].upper()
        Cron.convert_db_format_to_schedule(data)
        return data

    @private
    async def validate_rsync_task(self, data, schema):
        verrors = ValidationErrors()

        # Windows users can have spaces in their usernames
        # http://www.freebsd.org/cgi/query-pr.cgi?pr=164808

        username = data.get('user')
        if ' ' in username:
            verrors.add(f'{schema}.user', 'User names cannot have spaces')
            raise verrors

        user = await self.middleware.call(
            'notifier.get_user_object',
            username
        )
        if not user:
            verrors.add(f'{schema}.user', f'Provided user "{username}" does not exist')
            raise verrors

        remote_host = data.get('remotehost')
        if not remote_host:
            verrors.add(f'{schema}.remotehost', 'Please specify a remote host')

        if data.get('extra'):
            data['extra'] = ' '.join(data['extra'])
        else:
            data['extra'] = ''

        mode = data.get('mode')
        if not mode:
            verrors.add(f'{schema}.mode', 'This field is required')

        remote_module = data.get('remotemodule')
        if mode == 'MODULE' and not remote_module:
            verrors.add(f'{schema}.remotemodule', 'This field is required')

        if mode == 'SSH':
            remote_port = data.get('remoteport')
            if not remote_port:
                verrors.add(f'{schema}.remoteport', 'This field is required')

            remote_path = data.get('remotepath')
            if not remote_path:
                verrors.add(f'{schema}.remotepath', 'This field is required')

            search = os.path.join(user['pw_dir'], '.ssh', 'id_[edr]*')
            exclude_from_search = os.path.join(user['pw_dir'], '.ssh', 'id_[edr]*pub')
            key_files = set(glob.glob(search)) - set(glob.glob(exclude_from_search))
            if not key_files:
                verrors.add(
                    f'{schema}.user',
                    'In order to use rsync over SSH you need a user'
                    ' with a private key (DSA/ECDSA/RSA) set up in home dir.'
                )
            else:
                for file in glob.glob(search):
                    if '.pub' not in file:
                        # file holds a private key and it's permissions should be 600
                        if os.stat(file).st_mode & 0o077 != 0:
                            verrors.add(
                                f'{schema}.user',
                                f'Permissions {oct(os.stat(file).st_mode & 0o777)} for {file} are too open. Please '
                                f'correct them by running chmod 600 {file}'
                            )

            if(
                data.get('validate_rpath') and
                remote_path and
                remote_host and
                remote_port
            ):
                if '@' in remote_host:
                    remote_username, remote_host = remote_host.rsplit('@', 1)
                else:
                    remote_username = username

                try:
                    with (await asyncio.wait_for(asyncssh.connect(
                            remote_host,
                            port=remote_port,
                            username=remote_username,
                            client_keys=key_files,
                            known_hosts=None
                    ), timeout=5)) as conn:

                        await conn.run(f'test -d {shlex.quote(remote_path)}', check=True)

                except asyncio.TimeoutError:

                    verrors.add(
                        f'{schema}.remotehost',
                        'SSH timeout occurred. Remote path cannot be validated.'
                    )

                except OSError as e:

                    if e.errno == 113:
                        verrors.add(
                            f'{schema}.remotehost',
                            f'Connection to the remote host {remote_host} on port {remote_port} failed.'
                        )
                    else:
                        verrors.add(
                            f'{schema}.remotehost',
                            e.__str__()
                        )

                except asyncssh.DisconnectError as e:

                    verrors.add(
                        f'{schema}.remotehost',
                        f'Disconnect Error[ error code {e.code} ] was generated when trying to '
                        f'communicate with remote host {remote_host} and remote user {remote_username}.'
                    )

                except asyncssh.ProcessError as e:

                    if e.code == '1':
                        verrors.add(
                            f'{schema}.remotepath',
                            'The Remote Path you specified does not exist or is not a directory.'
                            'Either create one yourself on the remote machine or uncheck the '
                            'validate_rpath field'
                        )
                    else:
                        verrors.add(
                            f'{schema}.remotepath',
                            f'Connection to Remote Host was successful but failed to verify '
                            f'Remote Path. {e.__str__()}'
                        )

                except asyncssh.Error as e:

                    if e.__class__.__name__ in e.__str__():
                        exception_reason = e.__str__()
                    else:
                        exception_reason = e.__class__.__name__ + ' ' + e.__str__()
                    verrors.add(
                        f'{schema}.remotepath',
                        f'Remote Path could not be validated. An exception was raised. {exception_reason}'
                    )
            elif data.get('validate_rpath'):
                verrors.add(
                    f'{schema}.remotepath',
                    'Remote path could not be validated because of missing fields'
                )

        data.pop('validate_rpath', None)

        # Keeping compatibility with legacy UI
        for field in ('mode', 'direction'):
            data[field] = data[field].lower()

        return verrors, data

    @accepts(Dict(
        'rsync_task_create',
        Str('path', required=True),
        Str('user', required=True),
        Str('remotehost'),
        Int('remoteport'),
        Str('mode', enum=['MODULE', 'SSH'], default='MODULE'),
        Str('remotemodule'),
        Str('remotepath'),
        Bool('validate_rpath'),
        Str('direction', enum=['PULL', 'PUSH'], default='PUSH'),
        Str('desc'),
        Cron(
            'schedule',
            defaults={'minute': '00'},
        ),
        Bool('recursive'),
        Bool('times'),
        Bool('compress'),
        Bool('archive'),
        Bool('delete'),
        Bool('quiet'),
        Bool('preserveperm'),
        Bool('preserveattr'),
        Bool('delayupdates'),
        List('extra', items=[Str('extra')]),
        Bool('enabled'),
        register=True,
    ))
    async def do_create(self, data):
        """
        Create a Rsync Task.

        `path` represents the path to pool/dataset.

        `remotehost` is ip address or hostname of the remote system. If username differs on the remote host,
        "username@remote_host" format should be used.

        `mode` represents different operating mechanisms for Rsync i.e Rsync Module mode / Rsync SSH mode.

        `remotemodule` is the name of remote module, this attribute should be specified when `mode` is set to MODULE.

        `remotepath` specifies the path on the remote system.

        `validate_rpath` is a boolean which when sets validates the existence of the remote path.

        `direction` specifies if data should be PULLED or PUSHED from the remote system.

        `compress` when set reduces the size of the data which is to be transmitted.

        `archive` when set makes rsync run recursively, preserving symlinks, permissions, modification times, group,
        and special files.

        `delete` when set deletes files in the destination directory which do not exist in the source directory.

        `preserveperm` when set preserves original file permissions.

        .. examples(websocket)::

          Create a Rsync Task which pulls data from a remote system every 5 minutes.

            :::javascript
            {
                "id": "6841f242-840a-11e6-a437-00e04d680384",
                "msg": "method",
                "method": "rsynctask.create",
                "params": [{
                    "enabled": true,
                    "schedule": {
                        "minute": "5",
                        "hour": "*",
                        "dom": "*",
                        "month": "*",
                        "dow": "*"
                    },
                    "desc": "Test rsync task",
                    "user": "root",
                    "mode": "MODULE",
                    "remotehost": "root@192.168.0.10",
                    "compress": true,
                    "archive": true,
                    "direction": "PULL",
                    "path": "/mnt/vol1/rsync_dataset",
                    "remotemodule": "remote_module1"
                }]
            }
        """
        verrors, data = await self.validate_rsync_task(data, 'rsync_task_create')
        if verrors:
            raise verrors

        Cron.convert_schedule_to_db_format(data)

        data['id'] = await self.middleware.call(
            'datastore.insert',
            self._config.datastore,
            data,
            {'prefix': self._config.datastore_prefix}
        )
        await self.middleware.call('service.restart', 'cron')

        return await self._get_instance(data['id'])

    @accepts(
        Int('id', validators=[Range(min=1)]),
        Patch('rsync_task_create', 'rsync_task_update', ('attr', {'update': True}))
    )
    async def do_update(self, id, data):
        """
        Update Rsync Task of `id`.
        """
        old = await self.query(filters=[('id', '=', id)], options={'get': True})

        new = old.copy()
        new.update(data)

        verrors, data = await self.validate_rsync_task(new, 'rsync_task_update')
        if verrors:
            raise verrors

        Cron.convert_schedule_to_db_format(new)

        await self.middleware.call(
            'datastore.update',
            self._config.datastore,
            id,
            new,
            {'prefix': self._config.datastore_prefix}
        )
        await self.middleware.call('service.restart', 'cron')

        return await self.query(filters=[('id', '=', id)], options={'get': True})

    @accepts(Int('id'))
    async def do_delete(self, id):
        """
        Delete Rsync Task of `id`.
        """
        res = await self.middleware.call('datastore.delete', self._config.datastore, id)
        await self.middleware.call('service.restart', 'cron')
        return res

    @private
    async def commandline(self, id):
        """
        Helper method to generate the rsync command avoiding code duplication.
        """
        rsync = await self._get_instance(id)
        path = shlex.quote(rsync['path'])

        line = [
            '/usr/bin/lockf', '-s', '-t', '0', '-k', path, '/usr/local/bin/rsync'
        ]
        for name, flag in (
            ('archive', '-a'),
            ('compress', '-z'),
            ('delayupdates', '--delay-updates'),
            ('delete', '--delete-delay'),
            ('preserveattr', '-X'),
            ('preserveperm', '-p'),
            ('recursive', '-r'),
            ('times', '-t'),
        ):
            if rsync[name]:
                line.append(flag)
        if rsync['extra']:
            line.append(' '.join(rsync['extra']))

        # Do not use username if one is specified in host field
        # See #5096 for more details
        if '@' in rsync['remotehost']:
            remote = rsync['remotehost']
        else:
            remote = f'"{rsync["user"]}"@{rsync["remotehost"]}'

        if rsync['mode'] == 'MODULE':
            module_args = [path, f'{remote}::"{rsync["remotemodule"]}"']
            if rsync['direction'] != 'PUSH':
                module_args.reverse()
            line += module_args
        else:
            line += [
                '-e',
                f'"ssh -p {rsync["remoteport"]} -o BatchMode=yes -o StrictHostKeyChecking=yes"'
            ]
            path_args = [path, f'{remote}:"{shlex.quote(rsync["remotepath"])}"']
            if rsync['direction'] != 'PUSH':
                path_args.reverse()
            line += path_args

        if rsync['quiet']:
            line += ['>', '/dev/null', '2>&1']

        return ' '.join(line)

    @item_method
    @accepts(Int('id'))
    @job(lock=lambda args: args[-1], logs=True)
    def run(self, job, id):
        """
        Job to run rsync task of `id`.

        Output is saved to job log excerpt as well as syslog.
        """
        rsync = self.middleware.call_sync('rsynctask._get_instance', id)
        commandline = self.middleware.call_sync('rsynctask.commandline', id)

        cp = run_command_with_user_context(
            commandline, rsync['user'], lambda v: job.logs_fd.write(v)
        )

        if cp.returncode != 0:
            raise CallError(
                f'rsync command returned {cp.returncode}. Check logs for further information.'
            )
