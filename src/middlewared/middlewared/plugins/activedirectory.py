import datetime
import enum
import errno
import grp
import ipaddr
import ldap
import ldap.sasl
import ntplib
import pwd
import socket
import subprocess

from dns import resolver
from ldap.controls import SimplePagedResultsControl
from operator import itemgetter
from middlewared.schema import accepts, Bool, Dict, Int, List, Str
from middlewared.service import job, private, ConfigService, ValidationError, ValidationErrors
from middlewared.service_exception import CallError
from middlewared.utils import run, Popen


class DSStatus(enum.Enum):
    """
    Following items are used for cache entries indicating the status of the
    Directory Service.
    :FAULTED: Directory Service is enabled, but not HEALTHY.
    :LEAVING: Directory Service is in process of stopping.
    :JOINING: Directory Service is in process of starting.
    :HEALTHY: Directory Service is enabled, and last status check has passed.
    There is no "DISABLED" DSStatus because this is controlled by the "enable" checkbox.
    This is a design decision to avoid conflict between the checkbox and the cache entry.
    """
    FAULTED = 1
    LEAVING = 2
    JOINING = 3
    HEALTHY = 4


class neterr(enum.Enum):
    JOINED = 1
    NOTJOINED = 2
    FAULT = 3


class SRV(enum.Enum):
    DOMAINCONTROLLER = '_ldap._tcp.dc._msdcs.'
    FORESTGLOBALCATALOG = '_ldap._tcp.gc._msdcs.'
    GLOBALCATALOG = '_gc._tcp.'
    KERBEROS = '_kerberos._tcp.'
    KERBEROSDOMAINCONTROLLER = '_kerberos._tcp.dc._msdcs.'
    KPASSWD = '_kpasswd._tcp.'
    LDAP = '_ldap._tcp.'
    PDC = '_ldap._tcp.pdc._msdcs.'


class SSL(enum.Enum):
    NOSSL = 'OFF'
    USESSL = 'ON'
    USETLS = 'START_TLS'


class ActiveDirectory_DNS(object):
    def __init__(self, **kwargs):
        super(ActiveDirectory_DNS, self).__init__()
        self.ad = kwargs.get('conf')
        self.logger = kwargs.get('logger')
        return

    def _get_SRV_records(self, host, dns_timeout):
        """
        Set resolver timeout to 1/3 of the lifetime. The timeout defines
        how long to wait before moving on to the next nameserver in resolv.conf
        """
        srv_records = []

        if not host:
            return srv_records

        r = resolver.Resolver()
        r.lifetime = dns_timeout
        r.timeout = r.lifetime / 3

        try:

            answers = r.query(host, 'SRV')
            srv_records = sorted(
                answers,
                key=lambda a: (int(a.priority), int(a.weight))
            )

        except Exception:
            srv_records = []

        return srv_records

    def port_is_listening(self, host, port, timeout=1):
        ret = False

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if timeout:
            s.settimeout(timeout)

        try:
            s.connect((host, port))
            ret = True

        except Exception as e:
            raise CallError(e)

        finally:
            s.close()

        return ret

    def _get_servers(self, srv_prefix):
        """
        We will first try fo find servers based on our AD site. If we don't find
        a server in our site, then we populate list for whole domain. Ticket #27584
        Domain Controllers, Forest Global Catalog Servers, and Kerberos Domain Controllers
        need the site information placed before the 'msdcs' component of the host entry.t
        """
        servers = []
        if not self.ad['domainname']:
            return servers

        if self.ad['site'] and self.ad['site'] != 'Default-First-Site-Name':
            if 'msdcs' in srv_prefix.value:
                parts = srv_prefix.value.split('.')
                srv = '.'.join([parts[0], parts[1]])
                msdcs = '.'.join([parts[2], parts[3]])
                host = f"{srv}.{self.ad['site']}._sites.{msdcs}.{self.ad['domainname']}"
            else:
                host = f"{srv_prefix.value}{self.ad['site']}._sites.{self.ad['domainname']}"
        else:
            host = f"{srv_prefix.value}{self.ad['domainname']}"

        servers = self._get_SRV_records(host, self.ad['dns_timeout'])

        if not servers and self.ad['site']:
            host = f"{srv_prefix.value}{self.ad['domainname']}"
            servers = self._get_SRV_records(host, self.ad['dns_timeout'])

        if SSL(self.ad['ssl']) == SSL.USESSL:
            for server in servers:
                if server.port == 389:
                    server.port = 636

        return servers

    def get_n_working_servers(self, srv=SRV['DOMAINCONTROLLER'], number=1):
        """
        :get_n_working_servers: often only a few working servers are needed and not the whole
        list available on the domain. This takes the SRV record type and number of servers to get
        as arguments.
        """
        servers = self._get_servers(srv)
        found_servers = []
        for server in servers:
            if len(found_servers) == number:
                break

            host = server.target.to_text(True)
            port = int(server.port)
            if self.port_is_listening(host, port, timeout=1):
                server_info = {'host': host, 'port': port}
                found_servers.append(server_info)

        if self.ad['verbose_logging']:
            self.logger.debug(f'Request for [{number}] of server type [{srv.name}] returned: {found_servers}')
        return found_servers


class ActiveDirectory_LDAP(object):
    def __init__(self, **kwargs):
        super(ActiveDirectory_LDAP, self).__init__()
        self.ad = kwargs.get('ad_conf')
        self.hosts = kwargs.get('hosts')
        self.interfaces = kwargs.get('interfaces')
        self.logger = kwargs.get('logger')
        self.pagesize = 1024
        self._isopen = False
        self._handle = None
        self._rootDSE = None
        self._rootDomainNamingContext = None
        self._configurationNamingContext = None
        self._defaultNamingContext = None

    def __enter__(self):
        return self

    def __exit__(self, typ, value, traceback):
        if self._isopen:
            self._close()

    def validate_credentials(self):
        """
        :validate_credentials: simple check to determine whether we can establish
        an ldap session with the credentials that are in the configuration.
        """
        ret = self._open()
        if ret:
            self._close()
        return ret

    def _open(self):
        """
        We can only intialize a single host. In this case,
        we iterate through a list of hosts until we get one that
        works and then use that to set our LDAP handle.

        SASL GSSAPI bind only succeeds when DNS reverse lookup zone
        is correctly populated. Fall through to simple bind if this
        fails.
        """
        res = None
        if self._isopen:
            return True

        if self.hosts:
            saved_simple_error = None
            saved_gssapi_error = None
            for server in self.hosts:
                proto = 'ldaps' if SSL(self.ad['ssl']) == SSL.USESSL else 'ldap'
                uri = f"{proto}://{server['host']}:{server['port']}"
                try:
                    self._handle = ldap.initialize(uri)
                except Exception as e:
                    self.logger.debug(f'Failed to initialize ldap connection to [{uri}]: ({e}). Moving to next server.')
                    continue

                if self.ad['verbose_logging']:
                    self.logger.debug(f'Successfully initialized LDAP server: [{uri}]')

                res = None
                ldap.protocol_version = ldap.VERSION3
                ldap.set_option(ldap.OPT_REFERRALS, 0)
                ldap.set_option(ldap.OPT_NETWORK_TIMEOUT, 10.0)

                if SSL(self.ad['ssl']) != SSL.NOSSL:
                    ldap.set_option(ldap.OPT_X_TLS_ALLOW, 1)
                    ldap.set_option(
                        ldap.OPT_X_TLS_CACERTFILE,
                        f"/etc/certificates/{self.ad['certificate']['cert_name']}.crt"
                    )
                    ldap.set_option(
                        ldap.OPT_X_TLS_REQUIRE_CERT,
                        ldap.OPT_X_TLS_ALLOW
                    )

                if SSL(self.ad['ssl']) == SSL.USETLS:
                    try:
                        self._handle.start_tls_s()

                    except ldap.LDAPError as e:
                        self.logger.debug('%s', e)

                if self.ad['kerberos_principal']:
                    try:
                        self._handle.sasl_gssapi_bind_s()
                        if self.ad['verbose_logging']:
                            self.logger.debug(f'Successfully bound to [{uri}] using SASL GSSAPI.')
                        res = True
                        break
                    except Exception as e:
                        saved_gssapi_error = e
                        self.logger.debug(f'SASL GSSAPI bind failed: {e}. Attempting simple bind')

                bindname = f"{self.ad['bindname']}@{self.ad['domainname']}"
                try:
                    res = self._handle.simple_bind_s(bindname, self.ad['bindpw'])
                    if self.ad['verbose_logging']:
                        self.logger.debug(f'Successfully bound to [{uri}] using [{bindname}]')
                    break
                except Exception as e:
                    self.logger.debug(f'Failed to bind to [{uri}] using [{bindname}]')
                    saved_simple_error = e
                    continue

            if res:
                self._isopen = True
            elif saved_gssapi_error:
                raise CallError(saved_gssapi_error)
            elif saved_simple_error:
                raise CallError(saved_simple_error)

        return (self._isopen is True)

    def _close(self):
        self._isopen = False
        if self._handle:
            self._handle.unbind()
            self._handle = None

    def _search(self, basedn='', scope=ldap.SCOPE_SUBTREE, filter='', timeout=-1, sizelimit=0):
        if not self._handle:
            self._open()

        result = []
        serverctrls = None
        clientctrls = None
        paged = SimplePagedResultsControl(
            criticality=False,
            size=self.pagesize,
            cookie=''
        )
        paged_ctrls = {SimplePagedResultsControl.controlType: SimplePagedResultsControl}

        page = 0
        while True:
            serverctrls = [paged]

            id = self._handle.search_ext(
                basedn,
                scope,
                filterstr=filter,
                attrlist=None,
                attrsonly=0,
                serverctrls=serverctrls,
                clientctrls=clientctrls,
                timeout=timeout,
                sizelimit=sizelimit
            )

            (rtype, rdata, rmsgid, serverctrls) = self._handle.result3(
                id, resp_ctrl_classes=paged_ctrls
            )

            result.extend(rdata)

            paged.size = 0
            paged.cookie = cookie = None
            for sc in serverctrls:
                if sc.controlType == SimplePagedResultsControl.controlType:
                    cookie = sc.cookie
                    if cookie:
                        paged.cookie = cookie
                        paged.size = self.pagesize

                        break

            if not cookie:
                break

            page += 1

        return result

    def _get_sites(self, distinguishedname):
        sites = []
        basedn = f'CN=Sites,{self._configurationNamingContext}'
        filter = f'(&(objectClass=site)(distinguishedname={distinguishedname}))'
        results = self._search(basedn, ldap.SCOPE_SUBTREE, filter)
        if results:
            for r in results:
                if r[0]:
                    sites.append(r)
        return sites

    def _get_subnets(self):
        subnets = []
        ipv4_subnet_info_lst = []
        ipv6_subnet_info_lst = []
        baseDN = f'CN=Subnets,CN=Sites,{self._configurationNamingContext}'
        results = self._search(baseDN, ldap.SCOPE_SUBTREE, '(objectClass=subnet)')
        if results:
            for r in results:
                if r[0]:
                    subnets.append(r)

        for s in subnets:
            if not s or len(s) < 2:
                continue

            network = site_dn = None
            if 'cn' in s[1]:
                network = s[1]['cn'][0]
                if isinstance(network, bytes):
                    network = network.decode('utf-8')

            else:
                # if the network is None no point calculating
                # anything more so ....
                continue
            if 'siteObject' in s[1]:
                site_dn = s[1]['siteObject'][0]
                if isinstance(site_dn, bytes):
                    site_dn = site_dn.decode('utf-8')

            # Note should/can we do the same skip as done for `network`
            # the site_dn none too?
            st = ipaddr.IPNetwork(network)

            if st.version == 4:
                ipv4_subnet_info_lst.append({'site_dn': site_dn, 'network': st})
            elif st.version == 6:
                ipv4_subnet_info_lst.append({'site_dn': site_dn, 'network': st})

        if self.ad['verbose_logging']:
            self.logger.debug(f'ipv4_subnet_info: {ipv4_subnet_info_lst}')
            self.logger.debug(f'ipv6_subnet_info: {ipv6_subnet_info_lst}')
        return {'ipv4_subnet_info': ipv4_subnet_info_lst, 'ipv6_subnet_info': ipv6_subnet_info_lst}

    def _initialize_naming_context(self):
        self._rootDSE = self._search('', ldap.SCOPE_BASE, "(objectclass=*)")
        try:
            self._rootDomainNamingContext = self._rootDSE[0][1]['rootDomainNamingContext'][0].decode()
        except Exception as e:
            self.logger.debug(f'Failed to get rootDN: [{e}]')

        try:
            self._defaultNamingContext = self._rootDSE[0][1]['defaultNamingContext'][0].decode()
        except Exception as e:
            self.logger.debug(f'Failed to get baseDN: [{e}]')

        try:
            self._configurationNamingContext = self._rootDSE[0][1]['configurationNamingContext'][0].decode()
        except Exception as e:
            self.logger.debug(f'Failed to get configrationNamingContext: [{e}]')

        if self.ad['verbose_logging']:
            self.logger.debug(f'initialized naming context: rootDN:[{self._rootDomainNamingContext}]')
            self.logger.debug(f'baseDN:[{self._defaultNamingContext}], config:[{self._configurationNamingContext}]')

    def get_netbios_name(self):
        """
        :get_netbios_domain_name: returns the short form of the AD domain name. Confusingly
        titled 'nETBIOSName'. Must not be confused with the netbios hostname of the
        server. For this reason, API calls it 'netbios_domain_name'.
        """
        if not self._handle:
            self._open()
        self._initialize_naming_context()
        filter = f'(&(objectcategory=crossref)(nCName={self._defaultNamingContext}))'
        results = self._search(self._configurationNamingContext, ldap.SCOPE_SUBTREE, filter)
        try:
            netbios_name = results[0][1]['nETBIOSName'][0].decode()

        except Exception as e:
            self._close()
            self.logger.debug(f'Failed to discover short form of domain name: [{e}] res: [{results}]')
            netbios_name = None

        self._close()
        if self.ad['verbose_logging']:
            self.logger.debug(f'Query for nETBIOSName from LDAP returned: [{netbios_name}]')
        return netbios_name

    def locate_site(self):
        """
        Returns the AD site that the NAS is a member of. AD sites are used
        to break up large domains into managable chunks typically based on physical location.
        Although samba handles AD sites independent of the middleware. We need this
        information to determine which kerberos servers to use in the krb5.conf file to
        avoid communicating with a KDC on the other side of the world.
        In Windows environment, this is discovered via CLDAP query for closest DC. We
        can't do this, and so we have to rely on comparing our network configuration with
        site and subnet information obtained through LDAP queries.
        """
        if not self._handle:
            self._open()
        ipv4_site = None
        ipv6_site = None
        self._initialize_naming_context()
        subnets = self._get_subnets()
        for nic in self.interfaces:
            for alias in nic['aliases']:
                if alias['type'] == 'INET':
                    if ipv4_site is not None:
                        continue
                    ipv4_addr_obj = ipaddr.IPAddress(alias['address'], version=4)
                    for subnet in subnets['ipv4_subnet_info']:
                        if ipv4_addr_obj in subnet['network']:
                            sinfo = self._get_sites(distinguishedname=subnet['site_dn'])[0]
                            if sinfo and len(sinfo) > 1:
                                ipv4_site = sinfo[1]['cn'][0].decode()
                                break

                if alias['type'] == 'INET6':
                    if ipv6_site is not None:
                        continue
                    ipv6_addr_obj = ipaddr.IPAddress(alias['address'], version=6)
                    for subnet in subnets['ipv6_subnet_info']:
                        if ipv6_addr_obj in subnet['network']:
                            sinfo = self._get_sites(distinguishedname=subnet['site_dn'])[0]
                            if sinfo and len(sinfo) > 1:
                                ipv6_site = sinfo[1]['cn'][0].decode()
                                break

        if ipv4_site and ipv6_site and ipv4_site == ipv6_site:
            return ipv4_site

        if ipv4_site:
            return ipv4_site

        if not ipv4_site and ipv6_site:
            return ipv6_site

        return None


class ActiveDirectoryService(ConfigService):
    class Config:
        service = "activedirectory"
        datastore = 'directoryservice.activedirectory'
        datastore_extend = "activedirectory.ad_extend"
        datastore_prefix = "ad_"

    @private
    async def ad_extend(self, ad):
        smb = await self.middleware.call('smb.config')
        smb_ha_mode = await self.middleware.call('smb.get_smb_ha_mode')
        if smb_ha_mode == 'STANDALONE':
            ad.update({
                'netbiosname': smb['netbiosname'],
                'netbiosalias': smb['netbiosalias']
            })
        elif smb_ha_mode == 'UNIFIED':
            ngc = await self.middleware.call('network.configuration.config')
            ad.update({
                'netbiosname': ngc['hostname_virtual'],
                'netbiosalias': smb['netbiosalias']
            })
        elif smb_ha_mode == 'LEGACY':
            ngc = await self.middleware.call('network.configuration.config')
            ad.update({
                'netbiosname': ngc['hostname'],
                'netbiosname_b': ngc['hostname_b'],
                'netbiosalias': smb['netbiosalias']
            })

        for key in ['ssl', 'idmap_backend', 'nss_info', 'ldap_sasl_wrapping']:
            if key in ad and ad[key] is not None:
                ad[key] = ad[key].upper()

        for key in ['kerberos_realm', 'certificate']:
            if ad[key] is not None:
                ad[key] = ad[key]['id']

        return ad

    @private
    async def ad_compress(self, ad):
        """
        Convert kerberos realm to id. Force domain to upper-case. Remove
        foreign entries.
        kinit will fail if domain name is lower-case.
        """
        ad['domainname'] = ad['domainname'].upper()

        for key in ['netbiosname', 'netbiosalias', 'netbiosname_a', 'netbiosname_b']:
            if key in ad:
                ad.pop(key)

        for key in ['ssl', 'idmap_backend', 'nss_info', 'ldap_sasl_wrapping']:
            if ad[key] is not None:
                ad[key] = ad[key].lower()

        return ad

    @private
    async def update_netbios_data(self, old, new):
        smb_ha_mode = await self.middleware.call('smb.get_smb_ha_mode')
        must_update = False
        for key in ['netbiosname', 'netbiosalias', 'netbiosname_a', 'netbiosname_b']:
            if key in new and old[key] != new[key]:
                must_update = True

        if smb_ha_mode == 'STANDALONE' and must_update:
            await self.middleware.call(
                'smb.update',
                {
                    'netbiosname': new['netbiosname'],
                    'netbiosalias': new['netbiosalias']
                }
            )

        elif smb_ha_mode == 'UNIFIED' and must_update:
            await self.middleware.call('smb.update', 1, {'netbiosalias': new['netbiosalias']})
            await self.middleware.call('network.configuration', 1, {'hostname_virtual': new['netbiosname']})

        elif smb_ha_mode == 'LEGACY' and must_update:
            await self.middleware.call('smb.update', 1, {'netbiosalias': new['netbiosalias']})
            await self.middleware.call(
                'network.configuration',
                {
                    'hostname': new['netbiosname'],
                    'hostname_b': new['netbiosname_b']
                }
            )
        return

    @accepts(Dict(
        'activedirectory_update',
        Str('domainname', required=True),
        Str('bindname'),
        Str('bindpw', private=True),
        Str('ssl', default='OFF', enum=['OFF', 'ON', 'START_TLS']),
        Int('certificate', null=True),
        Bool('verbose_logging'),
        Bool('unix_extensions'),
        Bool('use_default_domain'),
        Bool('allow_trusted_doms'),
        Bool('allow_dns_updates'),
        Bool('disable_freenas_cache'),
        Str('site'),
        Int('kerberos_realm', null=True),
        Str('kerberos_principal', null=True),
        Int('timeout'),
        Int('dns_timeout'),
        Str('idmap_backend', default='RID', enum=['AD', 'AUTORID', 'FRUIT', 'LDAP', 'NSS', 'RFC2307', 'RID', 'SCRIPT']),
        Str('nss_info', null=True, default='', enum=['SFU', 'SFU20', 'RFC2307']),
        Str('ldap_sasl_wrapping', default='SIGN', enum=['PLAIN', 'SIGN', 'SEAL']),
        Str('createcomputer'),
        Str('netbiosname'),
        Str('netbiosname_b'),
        List('netbiosalias'),
        Bool('enable'),
        update=True
    ))
    async def do_update(self, data):
        """
        Update active directory configuration.
        `domainname` full DNS domain name of the Active Directory domain.

        `bindname` username used to perform the intial domain join.

        `bindpw` password used to perform the initial domain join.

        `ssl` encryption type for LDAP queries used in parameter autodetection during initial domain join.

        `certificate` certificate to use for LDAPS.

        `verbose_logging` increase logging during the domain join process.

        `use_default_domain` controls whether domain users and groups will have the pre-windows 2000 domain name prepended
        to the user account. When enabled, the user will appear as "administrator" rather than "EXAMPLE\administrator"

        `allow_trusted_doms` enable support for trusted domains. If this parameter is enabled, then separate idmap backends _must_
        be configured for each trusted domain, and the idmap cache should be cleared.

        `allow_dns_updates` during the domain join process, automatically generate DNS entries in the AD domain for the NAS. If
        this is disabled, then a domain administrator must manually add appropriate DNS entries for the NAS.

        `disable_freenas_cache` disable active caching of AD users and groups. When disabled, only users cached in winbind's internal
        cache will be visible in GUI dropdowns. Disabling active caching is recommended environments with a large amount of users.

        `site` AD site of which the NAS is a member. This parameter is auto-detected during the domain join process. If no AD site
        is configured for the subnet in which the NAS is configured, then this parameter will appear as 'Default-First-Site-Name'.
        Auto-detection is only performed during the initial domain join.

        `kerberos_realm` in which the server is located. This parameter will be automatically populated during the
        initial domain join. If the NAS has an AD site configured and that site has multiple kerberos servers, then the kerberos realm
        will be automatically updated with a site-specific configuration. Auto-detection is only performed during initial domain join.

        `kerberos_principal` kerberos principal to use for AD-related operations outside of Samba. After intial domain join, this field
        will be updated with the kerberos principal associated with the AD machine account for the NAS.

        `nss_info` controls how Winbind retrieves Name Service Information to construct a user's home directory and login shell. This parameter
        is only effective if the Active Directory Domain Controller supports the Microsoft Services for Unix (SFU) LDAP schema.
        :timeout: - timeout value for winbind-related operations. This value may need to be increased in  environments with high latencies
        for communications with domain controllers or a large number of domain controllers. Lowering the value may cause status checks to fail.

        `dns_timeout` timeout value for DNS queries during the initial domain join.

        `ldap_sasl_wrapping` defines whether ldap traffic will be signed or signed and encrypted (sealed).

        `createcomputer` Active Directory Organizational Unit in which to create the NAS computer object during domain join.
        If blank, then the default OU is used during computer account creation. Precreate the computer account in a specific OU.
        The OU string read from top to bottom without RDNs and delimited by a "/". E.g. "createcomputer=Computers/Servers/Unix NB: A backslash
        "\" is used as escape at multiple levels and may need to be doubled or even quadrupled. It is not used as a separator.

        `idmap_backend` provides a plugin interface for Winbind to use varying backends to store SID/uid/gid mapping tables.

        The Active Directory service will be started after a configuration update if the service was initially disabled, and the updated
        configuration sets :enable: to True. Likewise, the Active Directory service will be stopped if :enable: is changed to False
        during an update. If the configuration is updated, but the initial :enable: state was True, then only a samba_server restart
        command will be issued.
        """
        verrors = ValidationErrors()
        old = await self.config()
        new = old.copy()
        new.update(data)
        try:
            await self.update_netbios_data(old, new)
        except Exception as e:
            raise ValidationError('activedirectory_update.netbiosname', str(e))

        if new['enable']:
            if not new["bindpw"] and not new["kerberos_principal"]:
                raise ValidationError(
                    "activedirectory_update.bindname",
                    "Bind credentials or kerberos keytab are required to join an AD domain."
                )
            if new["bindpw"] and new["kerberos_principal"]:
                raise ValidationError(
                    "activedirectory_update.kerberos_principal",
                    "Simultaneous keytab and password authentication are not permitted."
                )

        if data['enable'] and not old['enable']:
            try:
                await self.middleware.run_in_thread(self.validate_credentials, new)
            except Exception as e:
                verrors.add("activedirectory_update.bindpw", f"Failed to validate bind credentials: {e}")
            try:
                await self.middleware.run_in_thread(self.validate_domain, new)
            except Exception as e:
                verrors.add("activedirectory_update", f"Failed to validate domain configuration: {e}")

        if verrors:
            raise verrors

        new = await self.ad_compress(new)
        await self.middleware.call(
            'datastore.update',
            'directoryservice.activedirectory',
            old['id'],
            new,
            {'prefix': 'ad_'}
        )

        start = False
        stop = False

        if old['idmap_backend'] != new['idmap_backend'].upper():
            idmap = await self.middleware.call('idmap.domaintobackend.query', [('domain', '=', 'DS_TYPE_ACTIVEDIRECTORY')])
            await self.middleware.call('idmap.domaintobackend.update', idmap[0]['id'], {'idmap_backend': new['idmap_backend'].upper()})

        if not old['enable']:
            if new['enable']:
                start = True
        else:
            if not new['enable']:
                stop = True

        if stop:
            await self.stop()
        if start:
            await self.start()

        if not stop and not start and new['enable']:
            await self.middleware.call('service.restart', 'cifs')

        return await self.config()

    @private
    async def _set_state(self, state):
        await self.middleware.call('cache.put', 'AD_State', state.name)

    @accepts()
    async def get_state(self):
        """
        Check the state of the AD Directory Service.
        See DSStatus for definitions of return values.
        :DISABLED: Service is not enabled.
        If for some reason, the cache entry indicating Directory Service state
        does not exist, re-run a status check to generate a key, then return it.
        """
        ad = await self.config()
        if not ad['enable']:
            return 'DISABLED'
        else:
            try:
                return (await self.middleware.call('cache.get', 'AD_State'))
            except KeyError:
                try:
                    await self.started()
                except Exception:
                    pass

            return (await self.middleware.call('cache.get', 'AD_State'))

    @private
    async def start(self):
        """
        Start AD service. In 'UNIFIED' HA configuration, only start AD service
        on active storage controller.
        """
        ad = await self.config()
        smb = await self.middleware.call('smb.config')
        smb_ha_mode = await self.middleware.call('smb.get_smb_ha_mode')
        if smb_ha_mode == 'UNIFIED':
            if await self.middleware.call('failover.status') != 'MASTER':
                return

        state = await self.get_state()
        if state in [DSStatus['JOINING'], DSStatus['LEAVING']]:
            raise CallError(f'Active Directory Service has status of [{state.value}]. Wait until operation completes.', errno.EBUSY)

        await self._set_state(DSStatus['JOINING'])
        await self.middleware.call('datastore.update', self._config.datastore, ad['id'], {'ad_enable': True})
        await self.middleware.call('etc.generate', 'hostname')

        """
        Kerberos realm field must be populated so that we can perform a kinit
        and use the kerberos ticket to execute 'net ads' commands.
        """
        if not ad['kerberos_realm']:
            realms = await self.middleware.call('kerberos.realm.query', [('realm', '=', ad['domainname'])])

            if realms:
                await self.middleware.call('datastore.update', self._config.datastore, ad['id'], {'ad_kerberos_realm': realms[0]['id']})
            else:
                await self.middleware.call('datastore.insert', 'directoryservice.kerberosrealm', {'krb_realm': ad['domainname'].upper()})
            ad = await self.config()

        await self.middleware.call('kerberos.start')

        """
        'workgroup' is the 'pre-Windows 2000 domain name'. It must be set to the nETBIOSName value in Active Directory.
        This must be properly configured in order for Samba to work correctly as an AD member server.
        'site' is the ad site of which the NAS is a member. If sites and subnets are unconfigured this will
        default to 'Default-First-Site-Name'.
        """

        if not ad['site']:
            new_site = await self.middleware.run_in_thread(self.get_site)
            if new_site != 'Default-First-Site-Name':
                ad = await self.config()
                site_indexed_kerberos_servers = await self.middleware.run_in_thread(self.get_kerberos_servers)

                if site_indexed_kerberos_servers:
                    await self.middleware.call(
                        'datastore.update',
                        'directoryservice.kerberosrealm',
                        ad['kerberos_realm']['id'],
                        site_indexed_kerberos_servers
                    )
                    await self.middleware.call('etc.generate', 'kerberos')

        if not smb['workgroup'] or smb['workgroup'] == 'WORKGROUP':
            await self.middleware.run_in_thread(self.get_netbios_domain_name)

        await self.middleware.call('etc.generate', 'smb')

        """
        Check response of 'net ads testjoin' to determine whether the server needs to be joined to Active Directory.
        Only perform the domain join if we receive the exact error code indicating that the server is not joined to
        Active Directory. 'testjoin' will fail if the NAS boots before the domain controllers in the environment.
        In this case, samba should be started, but the directory service reported in a FAULTED state.
        """

        ret = await self._net_ads_testjoin(smb['workgroup'])
        if ret == neterr.NOTJOINED:
            self.logger.debug(f"Test join to {ad['domainname']} failed. Performing domain join.")
            await self._net_ads_join()
            if smb_ha_mode != 'LEGACY':
                kt_id = await self.middleware.call('kerberos.keytab.store_samba_keytab')
                if kt_id:
                    self.logger.debug('Successfully generated keytab for computer account. Clearing bind credentials')
                    await self.middleware.call(
                        'datastore.update',
                        'directoryservice.activedirectory',
                        ad['id'],
                        {'ad_bindpw': '', 'ad_kerberos_principal': f'{smb["netbiosname"].upper()}$@{ad["domainname"]}'}
                    )
                    ad = await self.config()

            ret = neterr.JOINED
            await self.middleware.call('idmap.get_or_create_idmap_by_domain', 'DS_TYPE_ACTIVEDIRECTORY')
            await self.middleware.call('service.update', 'cifs', {'enable': True})
            try:
                await self.middleware.call('idmap.clear_idmap_cache')
            except Exception as e:
                self.logger.debug('Failed to clear idmap cache: %s', e)
            await self.middleware.call('service.update', 'cifs', {'enable': True})
            await self.middleware.run_in_thread(self.set_ntp_servers)
            if ad['allow_trusted_doms']:
                await self.middleware.call('idmap.autodiscover_trusted_domains')

        await self.middleware.call('service.restart', 'cifs')
        await self.middleware.call('etc.generate', 'pam')
        await self.middleware.call('etc.generate', 'nss')
        if ret == neterr.JOINED:
            await self._set_state(DSStatus['HEALTHY'])
            await self.get_cache()
        else:
            await self._set_state(DSStatus['FAULTED'])

    @private
    async def stop(self):
        ad = await self.config()
        await self.middleware.call('datastore.update', self._config.datastore, ad['id'], {'ad_enable': False})
        await self._set_state(DSStatus['LEAVING'])
        await self.middleware.call('etc.generate', 'hostname')
        await self.middleware.call('kerberos.stop')
        await self.middleware.call('etc.generate', 'smb')
        await self.middleware.call('service.restart', 'cifs')
        await self.middleware.call('etc.generate', 'pam')
        await self.middleware.call('etc.generate', 'nss')
        await self.middleware.call('cache.pop', 'AD_State')

    @private
    def validate_credentials(self, ad=None):
        """
        Performs test bind to LDAP server in AD environment. If a kerberos principal
        is defined, then we must get a kerberos ticket before trying to validate
        credentials. For this reason, we first generate the krb5.conf and krb5.keytab
        and then we perform a kinit using this principal.
        """
        ret = False
        if ad is None:
            ad = self.middleware.call_sync('activedirectory.config')

        if ad['kerberos_principal']:
            self.middleware.call_sync('etc.generate', 'kerberos')
            kinit = subprocess.run(['kinit', '--renewable', '-k', ad['kerberos_principal']], capture_output=True)
            if kinit.returncode != 0:
                raise CallError(
                    f'kinit with principal {ad["kerberos_principal"]} failed with error {kinit.stderr.decode()}'
                )

        dcs = ActiveDirectory_DNS(conf=ad, logger=self.logger).get_n_working_servers(SRV['DOMAINCONTROLLER'], 3)
        if not dcs:
            raise CallError('Failed to open LDAP socket to any DC in domain.')

        with ActiveDirectory_LDAP(ad_conf=ad, logger=self.logger, hosts=dcs) as AD_LDAP:
            ret = AD_LDAP.validate_credentials()

        return ret

    @private
    def check_clockskew(self, ad=None):
        """
        Uses DNS srv records to determine server with PDC emulator FSMO role and
        perform NTP query to determine current clockskew. Raises exception if
        clockskew exceeds 3 minutes, otherwise returns dict with hostname of
        PDC emulator, time as reported from PDC emulator, and time difference
        between the PDC emulator and the NAS.
        """
        permitted_clockskew = datetime.timedelta(minutes=3)
        nas_time = datetime.datetime.now()
        if not ad:
            ad = self.middleware.call_sync('activedirectory.config')

        pdc = ActiveDirectory_DNS(conf=ad, logger=self.logger).get_n_working_servers(SRV['PDC'], 1)
        c = ntplib.NTPClient()
        response = c.request(pdc[0]['host'])
        ntp_time = datetime.datetime.fromtimestamp(response.tx_time)
        clockskew = abs(ntp_time - nas_time)
        if clockskew > permitted_clockskew:
            raise CallError(f'Clockskew between {pdc[0]["host"]} and NAS exceeds 3 minutes')
        return {'pdc': str(pdc[0]['host']), 'timestamp': str(ntp_time), 'clockskew': str(clockskew)}

    @private
    def validate_domain(self, data=None):
        """
        Methods used to determine AD domain health.
        """
        self.middleware.call_sync('activedirectory.check_clockskew', data)

    @private
    async def _get_cached_srv_records(self, srv=SRV['DOMAINCONTROLLER']):
        """
        Avoid unecessary DNS lookups. These can potentially be expensive if DNS
        is flaky. Try site-specific results first, then try domain-wide ones.
        """
        servers = []
        if await self.middleware.call('cache.has_key', f'SRVCACHE_{srv.name}_SITE'):
            servers = await self.middleware.call('cache.get', f'SRVCACHE_{srv.name}_SITE')

        if not servers and await self.middleware.call('cache.has_key', f'SRVCACHE_{srv.name}'):
            servers = await self.middleware.call('cache.get', f'SRVCACHE_{srv.name}')

        return servers

    @private
    async def _set_cached_srv_records(self, srv=None, site=None, results=None):
        """
        Cache srv record lookups for 24 hours
        """
        if not srv:
            raise CallError('srv record type not specified', errno.EINVAL)

        if site:
            await self.middleware.call('cache.put', f'SRVCACHE_{srv.name}_SITE', results, 86400)
        else:
            await self.middleware.call('cache.put', f'SRVCACHE_{srv.name}', results, 86400)
        return True

    @accepts()
    async def started(self):
        """
        Issue a no-effect command to our DC. This checks if our secure channel connection to our
        domain controller is still alive. It has much less impact than wbinfo -t.
        Default winbind request timeout is 60 seconds, and can be adjusted by the smb4.conf parameter
        'winbind request timeout ='
        """
        netlogon_ping = await run(['wbinfo', '-P'], check=False)
        if netlogon_ping.returncode != 0:
            await self._set_state(DSStatus['FAULTED'])
            raise CallError(netlogon_ping.stderr.decode().strip('\n'))
        await self._set_state(DSStatus['HEALTHY'])
        return True

    @private
    async def _net_ads_join(self):
        ad = await self.config()
        if ad['createcomputer']:
            netads = await run([
                'net', '-k', '-U', ad['bindname'], '-d', '5',
                'ads', 'join', f'createcomputer={ad["createcomputer"]}',
                ad['domainname']], check=False)
        else:
            netads = await run([
                'net', '-k', '-U', ad['bindname'], '-d', '5',
                'ads', 'join', ad['domainname']], check=False)

        if netads.returncode != 0:
            await self._set_state(DSStatus['FAULTED'])
            raise CallError(f'Failed to join [{ad["domainname"]}]: [{netads.stdout.decode().strip()}]')

    @private
    async def _net_ads_testjoin(self, workgroup):
        ad = await self.config()
        netads = await run([
            'net', '-k', '-w', workgroup,
            '-d', '5', 'ads', 'testjoin', ad['domainname']],
            check=False
        )
        if netads.returncode != 0:
            errout = netads.stderr.decode().strip()
            self.logger.debug(f'net ads testjoin failed with error: [{errout}]')
            if '0xfffffff6' in errout:
                return neterr.NOTJOINED
            else:
                return neterr.FAULT

        return neterr.JOINED

    @private
    def get_netbios_domain_name(self):
        """
        The 'workgroup' parameter must be set correctly in order for AD join to
        succeed. This is based on the short form of the domain name, which was defined
        by the AD administrator who deployed originally deployed the AD enviornment.
        The only way to reliably get this is to query the LDAP server. This method
        queries and sets it.
        """

        ret = False
        ad = self.middleware.call_sync('activedirectory.config')
        smb = self.middleware.call_sync('smb.config')
        dcs = self.middleware.call_sync('activedirectory._get_cached_srv_records', SRV['DOMAINCONTROLLER'])
        set_new_cache = True if not dcs else False

        if not dcs:
            dcs = ActiveDirectory_DNS(conf=ad, logger=self.logger).get_n_working_servers(SRV['DOMAINCONTROLLER'], 3)

        if set_new_cache:
            self.middleware.call_sync('activedirectory._set_cached_srv_records', SRV['DOMAINCONTROLLER'], ad['site'], dcs)

        with ActiveDirectory_LDAP(ad_conf=ad, logger=self.logger, hosts=dcs) as AD_LDAP:
            ret = AD_LDAP.get_netbios_name()

        if ret and smb['workgroup'] != ret:
            self.logger.debug(f'Updating SMB workgroup to match the short form of the AD domain [{ret}]')
            self.middleware.call_sync('datastore.update', 'services.cifs', smb['id'], {'cifs_srv_workgroup': ret})

        return ret

    @private
    def get_kerberos_servers(self):
        """
        This returns at most 3 kerberos servers located in our AD site. This is to optimize
        kerberos configuration for locations where kerberos servers may span the globe and
        have equal DNS weighting. Since a single kerberos server may represent an unacceptable
        single point of failure, fall back to relying on normal DNS queries in this case.
        """
        ad = self.middleware.call_sync('activedirectory.config')
        AD_DNS = ActiveDirectory_DNS(conf=ad, logger=self.logger)
        krb_kdc = AD_DNS.get_n_working_servers(SRV['KERBEROSDOMAINCONTROLLER'], 3)
        krb_admin_server = AD_DNS.get_n_working_servers(SRV['KERBEROS'], 3)
        krb_kpasswd_server = AD_DNS.get_n_working_servers(SRV['KPASSWD'], 3)
        kdc = [i['host'] for i in krb_kdc]
        admin_server = [i['host'] for i in krb_admin_server]
        kpasswd = [i['host'] for i in krb_kpasswd_server]
        for servers in [kdc, admin_server, kpasswd]:
            if len(servers) == 1:
                return None

        return {'krb_kdc': kdc, 'krb_admin_server': admin_server, 'krb_kpasswd_server': kpasswd}

    @private
    def set_ntp_servers(self):
        """
        Appropriate time sources are a requirement for an AD environment. By default kerberos authentication
        fails if there is more than a 5 minute time difference between the AD domain and the member server.
        If the NTP servers are the default that we ship the NAS with. If this is the case, then we will
        discover the Domain Controller with the PDC emulator FSMO role and set it as the preferred NTP
        server for the NAS.
        """
        ntp_servers = self.middleware.call_sync('system.ntpserver.query')
        default_ntp_servers = list(filter(lambda x: 'freebsd.pool.ntp.org' in x['address'], ntp_servers))
        if len(ntp_servers) != 3 or len(default_ntp_servers) != 3:
            return

        ad = self.middleware.call_sync('activedirectory.config')
        pdc = ActiveDirectory_DNS(conf=ad, logger=self.logger).get_n_working_servers(SRV['PDC'], 1)
        self.middleware.call_sync('system.ntpserver.create', {'address': pdc[0]['host'], 'prefer': True})

    @private
    def get_site(self):
        """
        First, use DNS to identify domain controllers
        Then, find a domain controller that is listening for LDAP connection if this information is not cached.
        Then, perform an LDAP query to determine our AD site
        """
        ad = self.middleware.call_sync('activedirectory.config')
        i = self.middleware.call_sync('interfaces.query')
        dcs = self.middleware.call_sync('activedirectory._get_cached_srv_records', SRV['DOMAINCONTROLLER'])
        set_new_cache = True if not dcs else False

        if not dcs:
            dcs = ActiveDirectory_DNS(conf=ad, logger=self.logger).get_n_working_servers(SRV['DOMAINCONTROLLER'], 3)
        if not dcs:
            raise CallError('Failed to open LDAP socket to any DC in domain.')

        if set_new_cache:
            self.middleware.call_sync('activedirectory._set_cached_srv_records', SRV['DOMAINCONTROLLER'], ad['site'], dcs)

        with ActiveDirectory_LDAP(ad_conf=ad, logger=self.logger, hosts=dcs, interfaces=i) as AD_LDAP:
            site = AD_LDAP.locate_site()

        if not site:
            site = 'Default-First-Site-Name'

        if not ad['site']:
            self.middleware.call_sync(
                'datastore.update',
                'directoryservice.activedirectory',
                ad['id'],
                {'ad_site': site}
            )

        return site

    @accepts(
        Dict(
            'leave_ad',
            Str('username', required=True),
            Str('password', required=True, private=True)
        )
    )
    async def leave(self, data):
        """
        Leave Active Directory domain. This will remove computer
        object from AD and clear relevant configuration data from
        the NAS.
        This requires credentials for appropriately-privileged user.
        """
        ad = await self.config()
        principal = f'{data["username"]}@{ad["domainname"]}'
        ad_kinit = await Popen(
            ['/usr/bin/kinit', '--renewable', '--password-file=STDIN', principal],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE
        )
        output = await ad_kinit.communicate(input=data['password'].encode())
        if ad_kinit.returncode != 0:
            raise CallError(f"kinit for domain [{ad['domainname']}] with password failed: {output[1].decode()}")

        netads = await run(['/usr/local/bin/net', '-U', data['username'], '-k', 'ads', 'leave'], check=False)
        if netads.returncode != 0:
            raise CallError(f"Failed to leave domain: [{netads.stderr.decode()}]")

        krb_princ = await self.middleware.call(
            'kerberos.keytab.query',
            [('name', '=', 'AD_MACHINE_ACCOUNT')],
            {'get': True}
        )
        await self.middleware.call('datastore.delete', 'directoryservice.kerberoskeytab', krb_princ['id'])
        await self.middleware.call('datastore.delete', 'directoryservice.kerberosrealm', ad['kerberos_realm'])
        await self.middleware.call('activedirectory.stop')

        self.logger.debug(f"Successfully left domain: ad['domainname']")

    @private
    @job(lock='fill_ad_cache')
    def fill_cache(self, job, force=False):
        """
        Use UID2SID and GID2SID entries in Samba's gencache.tdb to populate the AD_cache.
        Since this can include IDs outside of our configured idmap domains (Local accounts
        will also appear here), there is a check to see if the ID is inside the idmap ranges
        configured for domains that are known to us. Some samba idmap backends support
        id_type_both, in which case the will be GID2SID entries for AD users. getent group
        succeeds in this case (even though the group doesn't exist in AD). Since these
        we don't want to populate the UI cache with these entries, try to getpwnam for
        GID2SID entries. If it's an actual group, getpwnam will fail. This heuristic
        may be revised in the future, but we want to keep things as simple as possible
        here since the list of entries numbers perhaps in the tens of thousands.
        """
        if self.middleware.call_sync('cache.has_key', 'AD_cache') and not force:
            raise CallError('AD cache already exists. Refusing to generate cache.')

        self.middleware.call_sync('cache.pop', 'AD_cache')
        ad = self.middleware.call_sync('activedirectory.config')
        smb = self.middleware.call_sync('smb.config')
        if not ad['disable_freenas_cache']:
            """
            These calls populate the winbindd cache
            """
            pwd.getpwall()
            grp.getgrall()
        netlist = subprocess.run(
            ['net', 'cache', 'list'],
            capture_output=True,
            check=False
        )
        if netlist.returncode != 0:
            raise CallError(f'Winbind cache dump failed with error: {netlist.stderr.decode().strip()}')

        known_domains = []
        local_users = self.middleware.call_sync('user.query')
        local_groups = self.middleware.call_sync('group.query')
        cache_data = {'users': [], 'groups': []}
        configured_domains = self.middleware.call_sync('idmap.get_configured_idmap_domains')
        user_next_index = group_next_index = 300000000
        for d in configured_domains:
            if d['domain']['idmap_domain_name'] == 'DS_TYPE_ACTIVEDIRECTORY':
                known_domains.append({
                    'domain': smb['workgroup'],
                    'low_id': d['backend_data']['range_low'],
                    'high_id': d['backend_data']['range_high'],
                })
            elif d['domain']['idmap_domain_name'] not in ['DS_TYPE_DEFAULT_DOMAIN', 'DS_TYPE_LDAP']:
                known_domains.append({
                    'domain': d['domain']['idmap_domain_name'],
                    'low_id': d['backend_data']['range_low'],
                    'high_id': d['backend_data']['range_high'],
                })

        for line in netlist.stdout.decode().splitlines():
            if 'UID2SID' in line:
                cached_uid = ((line.split())[1].split('/'))[2]
                """
                Do not cache local users. This is to avoid problems where a local user
                may enter into the id range allotted to AD users.
                """
                is_local_user = any(filter(lambda x: x['uid'] == int(cached_uid), local_users))
                if is_local_user:
                    continue

                for d in known_domains:
                    if int(cached_uid) in range(d['low_id'], d['high_id']):
                        """
                        Samba will generate UID and GID cache entries when idmap backend
                        supports id_type_both.
                        """
                        try:
                            user_data = pwd.getpwuid(int(cached_uid))
                            cache_data['users'].append({
                                'id': user_next_index,
                                'uid': user_data.pw_uid,
                                'username': user_data.pw_name,
                                'unixhash': None,
                                'smbhash': None,
                                'group': {},
                                'home': '',
                                'shell': '',
                                'full_name': user_data.pw_gecos,
                                'builtin': False,
                                'email': '',
                                'password_disabled': False,
                                'locked': False,
                                'sudo': False,
                                'microsoft_account': False,
                                'attributes': {},
                                'groups': [],
                                'sshpubkey': None,
                                'local': False
                            })
                            user_next_index += 1
                            break
                        except Exception:
                            break

            if 'GID2SID' in line:
                cached_gid = ((line.split())[1].split('/'))[2]
                is_local_group = any(filter(lambda x: x['gid'] == int(cached_gid), local_groups))
                if is_local_group:
                    continue

                for d in known_domains:
                    if int(cached_gid) in range(d['low_id'], d['high_id']):
                        """
                        Samba will generate UID and GID cache entries when idmap backend
                        supports id_type_both. Actual groups will return key error on
                        attempt to generate passwd struct.
                        """
                        try:
                            pwd.getpwuid(int(cached_gid))
                            break
                        except Exception:
                            group_data = grp.getgrgid(int(cached_gid))
                            cache_data['groups'].append({
                                'id': group_next_index,
                                'gid': group_data.gr_gid,
                                'group': group_data.gr_name,
                                'builtin': False,
                                'sudo': False,
                                'users': [],
                                'local': False,
                            })
                            group_next_index += 1
                            break

        if not cache_data.get('users'):
            return

        sorted_cache = {}
        sorted_cache.update({
            'users': sorted(cache_data['users'], key=itemgetter('username'))
        })
        sorted_cache.update({
            'groups': sorted(cache_data['groups'], key=itemgetter('group'))
        })

        self.middleware.call_sync('cache.put', 'AD_cache', sorted_cache)

    @private
    async def get_cache(self):
        """
        Returns cached AD user and group information. If proactive caching is enabled
        then this will contain all AD users and groups, otherwise it contains the
        users and groups that were present in the winbindd cache when the cache was
        last filled. The cache expires and is refilled every 24 hours, or can be
        manually refreshed by calling fill_cache(True).
        """
        if not await self.middleware.call('cache.has_key', 'AD_cache'):
            await self.middleware.call('activedirectory.fill_cache')
            self.logger.debug('cache fill is in progress.')
            return {'users': [], 'groups': []}
        return await self.middleware.call('cache.get', 'AD_cache')
