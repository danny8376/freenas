import binascii
import bsd
from bsd import acl
import errno
import grp
import os
import pwd
import select
import shutil
import subprocess

from middlewared.main import EventSource
from middlewared.schema import Bool, Dict, Int, Ref, List, Str, UnixPerm, accepts
from middlewared.service import private, CallError, Service, job
from middlewared.utils import filter_list


class FilesystemService(Service):

    @accepts(Str('path', required=True), Ref('query-filters'), Ref('query-options'))
    def listdir(self, path, filters=None, options=None):
        """
        Get the contents of a directory.

        Each entry of the list consists of:
          name(str): name of the file
          path(str): absolute path of the entry
          realpath(str): absolute real path of the entry (if SYMLINK)
          type(str): DIRECTORY | FILESYSTEM | SYMLINK | OTHER
          size(int): size of the entry
          mode(int): file mode/permission
          uid(int): user id of entry owner
          gid(int): group id of entry onwer
        """
        if not os.path.exists(path):
            raise CallError(f'Directory {path} does not exist', errno.ENOENT)

        if not os.path.isdir(path):
            raise CallError(f'Path {path} is not a directory', errno.ENOTDIR)

        rv = []
        for entry in os.scandir(path):
            if entry.is_dir():
                etype = 'DIRECTORY'
            elif entry.is_file():
                etype = 'FILE'
            elif entry.is_symlink():
                etype = 'SYMLINK'
            else:
                etype = 'OTHER'

            data = {
                'name': entry.name,
                'path': entry.path,
                'realpath': os.path.realpath(entry.path) if etype == 'SYMLINK' else entry.path,
                'type': etype,
            }
            try:
                stat = entry.stat()
                data.update({
                    'size': stat.st_size,
                    'mode': stat.st_mode,
                    'uid': stat.st_uid,
                    'gid': stat.st_gid,
                })
            except FileNotFoundError:
                data.update({'size': None, 'mode': None, 'uid': None, 'gid': None})
            rv.append(data)
        return filter_list(rv, filters=filters or [], options=options or {})

    @accepts(Str('path'))
    def stat(self, path):
        """
        Return the filesystem stat(2) for a given `path`.
        """
        try:
            stat = os.stat(path, follow_symlinks=False)
        except FileNotFoundError:
            raise CallError(f'Path {path} not found', errno.ENOENT)

        stat = {
            'size': stat.st_size,
            'mode': stat.st_mode,
            'uid': stat.st_uid,
            'gid': stat.st_gid,
            'atime': stat.st_atime,
            'mtime': stat.st_mtime,
            'ctime': stat.st_ctime,
            'dev': stat.st_dev,
            'inode': stat.st_ino,
            'nlink': stat.st_nlink,
        }

        try:
            stat['user'] = pwd.getpwuid(stat['uid']).pw_name
        except KeyError:
            stat['user'] = None

        try:
            stat['group'] = grp.getgrgid(stat['gid']).gr_name
        except KeyError:
            stat['group'] = None

        stat['acl'] = False if self.middleware.call_sync('filesystem.acl_is_trivial', path) else True

        return stat

    @private
    @accepts(
        Str('path'),
        Str('content'),
        Dict(
            'options',
            Bool('append', default=False),
            Int('mode'),
        ),
    )
    def file_receive(self, path, content, options=None):
        """
        Simplified file receiving method for small files.

        `content` must be a base 64 encoded file content.

        DISCLAIMER: DO NOT USE THIS FOR BIG FILES (> 500KB).
        """
        options = options or {}
        dirname = os.path.dirname(path)
        if not os.path.exists(dirname):
            os.makedirs(dirname)
        if options.get('append'):
            openmode = 'ab'
        else:
            openmode = 'wb+'
        with open(path, openmode) as f:
            f.write(binascii.a2b_base64(content))
        mode = options.get('mode')
        if mode:
            os.chmod(path, mode)
        return True

    @private
    @accepts(
        Str('path'),
        Dict(
            'options',
            Int('offset'),
            Int('maxlen'),
        ),
    )
    def file_get_contents(self, path, options=None):
        """
        Get contents of a file `path` in base64 encode.

        DISCLAIMER: DO NOT USE THIS FOR BIG FILES (> 500KB).
        """
        options = options or {}
        if not os.path.exists(path):
            return None
        with open(path, 'rb') as f:
            if options.get('offset'):
                f.seek(options['offset'])
            data = binascii.b2a_base64(f.read(options.get('maxlen'))).decode().strip()
        return data

    @accepts(Str('path'))
    @job(pipes=["output"])
    async def get(self, job, path):
        """
        Job to get contents of `path`.
        """

        if not os.path.isfile(path):
            raise CallError(f'{path} is not a file')

        with open(path, 'rb') as f:
            await self.middleware.run_in_thread(shutil.copyfileobj, f, job.pipes.output.w)

    @accepts(
        Str('path'),
        Dict(
            'options',
            Bool('append', default=False),
            Int('mode'),
        ),
    )
    @job(pipes=["input"])
    async def put(self, job, path, options=None):
        """
        Job to put contents to `path`.
        """
        options = options or {}
        dirname = os.path.dirname(path)
        if not os.path.exists(dirname):
            os.makedirs(dirname)
        if options.get('append'):
            openmode = 'ab'
        else:
            openmode = 'wb+'

        with open(path, openmode) as f:
            await self.middleware.run_in_thread(shutil.copyfileobj, job.pipes.input.r, f)

        mode = options.get('mode')
        if mode:
            os.chmod(path, mode)
        return True

    @accepts(Str('path'))
    def statfs(self, path):
        """
        Return stats from the filesystem of a given path.

        Raises:
            CallError(ENOENT) - Path not found
        """
        try:
            statfs = bsd.statfs(path)
        except FileNotFoundError:
            raise CallError('Path not found.', errno.ENOENT)
        return {
            **statfs.__getstate__(),
            'total_bytes': statfs.total_blocks * statfs.blocksize,
            'free_bytes': statfs.free_blocks * statfs.blocksize,
            'avail_bytes': statfs.avail_blocks * statfs.blocksize,
        }

    def __convert_to_basic_permset(self, permset):
        """
        Convert "advanced" ACL permset format to basic format using
        bitwise operation and constants defined in py-bsd/bsd/acl.pyx,
        py-bsd/defs.pxd and acl.h.

        If the advanced ACL can't be converted without losing
        information, we return 'OTHER'.

        Reverse process converts the constant's value to a dictionary
        using a bitwise operation.
        """
        perm = 0
        for k, v, in permset.items():
            if v:
                perm |= acl.NFS4Perm[k]

        try:
            SimplePerm = (acl.NFS4BasicPermset(perm)).name
        except Exception:
            SimplePerm = 'OTHER'

        return SimplePerm

    def __convert_to_basic_flagset(self, flagset):
        flags = 0
        for k, v, in flagset.items():
            if k == "INHERITED":
                continue
            if v:
                flags |= acl.NFS4Flag[k]

        try:
            SimpleFlag = (acl.NFS4BasicFlagset(flags)).name
        except Exception:
            SimpleFlag = 'OTHER'

        return SimpleFlag

    def __convert_to_adv_permset(self, basic_perm):
        permset = {}
        perm_mask = acl.NFS4BasicPermset[basic_perm].value
        for name, member in acl.NFS4Perm.__members__.items():
            if perm_mask & member.value:
                permset.update({name: True})
            else:
                permset.update({name: False})

        return permset

    def __convert_to_adv_flagset(self, basic_flag):
        flagset = {}
        flag_mask = acl.NFS4BasicFlagset[basic_flag].value
        for name, member in acl.NFS4Flag.__members__.items():
            if flag_mask & member.value:
                flagset.update({name: True})
            else:
                flagset.update({name: False})

        return flagset

    @accepts(Str('path'))
    def acl_is_trivial(self, path):
        """
        ACL is trivial if it can be fully expressed as a file mode without losing
        any access rules. This is intended to be used as a check before allowing
        users to chmod() through the webui
        """
        if not os.path.exists(path):
            raise CallError('Path not found.', errno.ENOENT)
        a = acl.ACL(file=path)
        return a.is_trivial

    @accepts(
        Dict(
            'filesystem_ownership',
            Str('path', required=True),
            Int('uid', null=True, default=None),
            Int('gid', null=True, default=None),
            Dict(
                'options',
                Bool('recursive', default=False),
                Bool('traverse', default=False)
            )
        )
    )
    def chown(self, data):
        """
        Change owner or group of file at `path`.

        `uid` and `gid` specify new owner of the file. If either
        key is absent or None, then existing value on the file is not
        changed.

        `recursive` performs action recursively, but does
        not traverse filesystem mount points.

        If `traverse` and `recursive` are specified, then the chown
        operation will traverse filesystem mount points.
        """
        uid = -1 if data['uid'] is None else data['uid']
        gid = -1 if data['gid'] is None else data['gid']
        options = data['options']

        if not options['recursive']:
            os.chown(data['path'], uid, gid)
        else:
            winacl = subprocess.run([
                '/usr/local/bin/winacl',
                '-a', 'chown',
                '-O', str(uid), '-G', str(gid),
                '-rx' if options['traverse'] else '-r',
                '-p', data['path']], check=False, capture_output=True
            )
            if winacl.returncode != 0:
                raise CallError(f"Failed to recursively change ownership: {winacl.stderr.decode()}")

    @accepts(
        Dict(
            'filesystem_permission',
            Str('path', required=True),
            UnixPerm('mode', null=True),
            Int('uid', null=True, default=None),
            Int('gid', null=True, default=None),
            Dict(
                'options',
                Bool('stripacl', default=False),
                Bool('recursive', default=False),
                Bool('traverse', default=False),
            )
        )
    )
    @job(lock=lambda args: f'setperm:{args[0]}')
    def setperm(self, job, data):
        """
        Remove extended ACL from specified path.

        If `mode` is specified then the mode will be applied to the
        path and files and subdirectories depending on which `options` are
        selected. Mode should be formatted as string representation of octal
        permissions bits.

        `stripacl` setperm will fail if an extended ACL is present on `path`,
        unless `stripacl` is set to True.

        `recursive` remove ACLs recursively, but do not traverse dataset
        boundaries.

        `traverse` remove ACLs from child datasets.

        If no `mode` is set, and `stripacl` is True, then non-trivial ACLs
        will be converted to trivial ACLs. An ACL is trivial if it can be
        expressed as a file mode without losing any access rules.

        """
        options = data['options']
        mode = data.get('mode', None)

        uid = -1 if data['uid'] is None else data['uid']
        gid = -1 if data['gid'] is None else data['gid']

        if not os.path.exists(data['path']):
            raise CallError('Path not found.', errno.ENOENT)

        acl_is_trivial = self.middleware.call_sync('filesystem.acl_is_trivial', data['path'])
        if not acl_is_trivial and not options['stripacl']:
            raise CallError(
                f'Non-trivial ACL present on [{data["path"]}]. Option "stripacl" required to change permission.'
            )

        if mode is not None:
            mode = int(mode, 8)

        a = acl.ACL(file=data['path'])
        a.strip()
        a.apply(data['path'])

        if mode:
            os.chmod(data['path'], mode)

        if uid or gid:
            os.chown(data['path'], uid, gid)

        if not options['recursive']:
            return

        winacl = subprocess.run([
            '/usr/local/bin/winacl',
            '-a', 'clone' if mode else 'strip',
            '-O', str(uid), '-G', str(gid),
            '-rx' if options['traverse'] else '-r',
            '-p', data['path']], check=False, capture_output=True
        )
        if winacl.returncode != 0:
            raise CallError(f"Failed to recursively apply ACL: {winacl.stderr.decode()}")

    @accepts(
        Str('path'),
        Bool('simplified', default=True),
    )
    def getacl(self, path, simplified=True):
        """
        Return ACL of a given path.

        Simplified returns a shortened form of the ACL permset and flags

        `TRAVERSE` sufficient rights to traverse a directory, but not read contents.

        `READ` sufficient rights to traverse a directory, and read file contents.

        `MODIFIY` sufficient rights to traverse, read, write, and modify a file. Equivalent to modify_set.

        `FULL_CONTROL` all permissions.

        If the permisssions do not fit within one of the pre-defined simplified permissions types, then
        the full ACL entry will be returned.

        In all cases we replace USER_OBJ, GROUP_OBJ, and EVERYONE with owner@, group@, everyone@ for
        consistency with getfacl and setfacl. If one of aforementioned special tags is used, 'id' must
        be set to None.

        An inheriting empty everyone@ ACE is appended to non-trivial ACLs in order to enforce Windows
        expectations regarding permissions inheritance. This entry is removed from NT ACL returned
        to SMB clients when 'ixnas' samba VFS module is enabled. We also remove it here to avoid confusion.
        """
        if not os.path.exists(path):
            raise CallError('Path not found.', errno.ENOENT)

        stat = os.stat(path)

        a = acl.ACL(file=path)
        fs_acl = a.__getstate__()

        if not simplified:
            advanced_acl = []
            for entry in fs_acl:
                ace = {
                    'tag': (acl.ACLWho[entry['tag']]).value,
                    'id': entry['id'],
                    'type': entry['type'],
                    'perms': entry['perms'],
                    'flags': entry['flags'],
                }
                if ace['tag'] == 'everyone@' and self.__convert_to_basic_permset(ace['perms']) == 'NOPERMS':
                    continue

                advanced_acl.append(ace)

            return {'uid': stat.st_uid, 'gid': stat.st_gid, 'acl': advanced_acl}

        if simplified:
            simple_acl = []
            for entry in fs_acl:
                ace = {
                    'tag': (acl.ACLWho[entry['tag']]).value,
                    'id': entry['id'],
                    'type': entry['type'],
                    'perms': {'BASIC': self.__convert_to_basic_permset(entry['perms'])},
                    'flags': {'BASIC': self.__convert_to_basic_flagset(entry['flags'])},
                }
                if ace['tag'] == 'everyone@' and ace['perms']['BASIC'] == 'NOPERMS':
                    continue

                for key in ['perms', 'flags']:
                    if ace[key]['BASIC'] == 'OTHER':
                        ace[key] = entry[key]

                simple_acl.append(ace)

            return {'uid': stat.st_uid, 'gid': stat.st_gid, 'acl': simple_acl}

    @accepts(
        Dict(
            'filesystem_acl',
            Str('path', required=True),
            List(
                'dacl',
                items=[
                    Dict(
                        'aclentry',
                        Str('tag', enum=['owner@', 'group@', 'everyone@', 'USER', 'GROUP']),
                        Int('id', null=True),
                        Str('type', enum=['ALLOW', 'DENY']),
                        Dict(
                            'perms',
                            Bool('READ_DATA'),
                            Bool('WRITE_DATA'),
                            Bool('APPEND_DATA'),
                            Bool('READ_NAMED_ATTRS'),
                            Bool('WRITE_NAMED_ATTRS'),
                            Bool('EXECUTE'),
                            Bool('DELETE_CHILD'),
                            Bool('READ_ATTRIBUTES'),
                            Bool('WRITE_ATTRIBUTES'),
                            Bool('DELETE'),
                            Bool('READ_ACL'),
                            Bool('WRITE_ACL'),
                            Bool('WRITE_OWNER'),
                            Bool('SYNCHRONIZE'),
                            Str('BASIC', enum=['FULL_CONTROL', 'MODIFY', 'READ', 'TRAVERSE']),
                        ),
                        Dict(
                            'flags',
                            Bool('FILE_INHERIT'),
                            Bool('DIRECTORY_INHERIT'),
                            Bool('NO_PROPAGATE_INHERIT'),
                            Bool('INHERIT_ONLY'),
                            Bool('INHERITED'),
                            Str('BASIC', enum=['INHERIT', 'NOINHERIT']),
                        ),
                    )
                ],
                default=[]
            ),
            Int('uid', null=True, default=None),
            Int('gid', null=True, default=None),
            Dict(
                'options',
                Bool('stripacl', default=False),
                Bool('recursive', default=False),
                Bool('traverse', default=False),
            )
        )
    )
    @job(lock=lambda args: f'setacl:{args[0]}')
    def setacl(self, job, data):
        """
        Set ACL of a given path. Takes the following parameters:
        `path` full path to directory or file.

        `dacl` "simplified" ACL here or a full ACL.

        `uid` the desired UID of the file user. If set to -1, then UID is not changed.

        `gid` the desired GID of the file group. If set to -1 then GID is not changed.

        `recursive` apply the ACL recursively

        `traverse` traverse filestem boundaries (ZFS datasets)

        `strip` convert ACL to trivial. ACL is trivial if it can be expressed as a file mode without
        losing any access rules.

        In all cases we replace USER_OBJ, GROUP_OBJ, and EVERYONE with owner@, group@, everyone@ for
        consistency with getfacl and setfacl. If one of aforementioned special tags is used, 'id' must
        be set to None.

        An inheriting empty everyone@ ACE is appended to non-trivial ACLs in order to enforce Windows
        expectations regarding permissions inheritance. This entry is removed from NT ACL returned
        to SMB clients when 'ixnas' samba VFS module is enabled.
        """
        options = data['options']
        dacl = data.get('dacl', [])
        if not os.path.exists(data['path']):
            raise CallError('Path not found.', errno.ENOENT)

        if dacl and options['stripacl']:
            raise CallError('Setting ACL and stripping ACL are not permitted simultaneously.', errno.EINVAL)

        uid = -1 if data.get('uid', None) is None else data['uid']
        gid = -1 if data.get('gid', None) is None else data['gid']
        if options['stripacl']:
            a = acl.ACL(file=data['path'])
            a.strip()
            a.apply(data['path'])
        else:
            cleaned_acl = []
            lockace_is_present = False
            for entry in dacl:
                if entry['perms'].get('BASIC') == 'OTHER' or entry['flags'].get('BASIC') == 'OTHER':
                    raise CallError('Unable to apply simplified ACL due to OTHER entry. Use full ACL.', errno.EINVAL)
                ace = {
                    'tag': (acl.ACLWho(entry['tag'])).name,
                    'id': entry['id'],
                    'type': entry['type'],
                    'perms': self.__convert_to_adv_permset(entry['perms']['BASIC']) if 'BASIC' in entry['perms'] else entry['perms'],
                    'flags': self.__convert_to_adv_flagset(entry['flags']['BASIC']) if 'BASIC' in entry['flags'] else entry['flags'],
                }
                if ace['tag'] == 'EVERYONE' and self.__convert_to_basic_permset(ace['perms']) == 'NOPERMS':
                    lockace_is_present = True
                cleaned_acl.append(ace)
            if not lockace_is_present:
                locking_ace = {
                    'tag': 'EVERYONE',
                    'id': None,
                    'type': 'ALLOW',
                    'perms': self.__convert_to_adv_permset('NOPERMS'),
                    'flags': self.__convert_to_adv_flagset('INHERIT')
                }
                cleaned_acl.append(locking_ace)

            a = acl.ACL()
            a.__setstate__(cleaned_acl)
            a.apply(data['path'])

        if not options['recursive']:
            return True

        winacl = subprocess.run([
            '/usr/local/bin/winacl',
            '-a', 'clone', '-O', str(uid), '-G', str(gid),
            '-rx' if options['traverse'] else '-r',
            '-p', data['path']], check=False, capture_output=True
        )
        if winacl.returncode != 0:
            raise CallError(f"Failed to recursively apply ACL: {winacl.stderr.decode()}")


class FileFollowTailEventSource(EventSource):

    def run(self):
        if ':' in self.arg:
            path, lines = self.arg.rsplit(':', 1)
            lines = int(lines)
        else:
            path = self.arg
            lines = 3
        if not os.path.exists(path):
            # FIXME: Error?
            return

        bufsize = 8192
        fsize = os.stat(path).st_size
        if fsize < bufsize:
            bufsize = fsize
        i = 0
        with open(path) as f:
            data = []
            while True:
                i += 1
                if bufsize * i > fsize:
                    break
                f.seek(fsize - bufsize * i)
                data.extend(f.readlines())
                if len(data) >= lines or f.tell() == 0:
                    break

            self.send_event('ADDED', fields={'data': ''.join(data[-lines:])})
            f.seek(fsize)

            kqueue = select.kqueue()

            ev = [select.kevent(
                f.fileno(),
                filter=select.KQ_FILTER_VNODE,
                flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE | select.KQ_EV_CLEAR,
                fflags=select.KQ_NOTE_DELETE | select.KQ_NOTE_EXTEND | select.KQ_NOTE_WRITE | select.KQ_NOTE_ATTRIB,
            )]
            kqueue.control(ev, 0, 0)

            while not self._cancel.is_set():
                events = kqueue.control([], 1, 1)
                if not events:
                    continue
                # TODO: handle other file operations other than just extend/write
                self.send_event('ADDED', fields={'data': f.read()})


def setup(middleware):
    middleware.register_event_source('filesystem.file_tail_follow', FileFollowTailEventSource)
