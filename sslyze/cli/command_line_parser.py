# -*- coding: utf-8 -*-
from optparse import OptionParser, OptionGroup

import socket

from nassl.ssl_client import OpenSslFileTypeEnum
from typing import Text
from typing import Tuple
from sslyze.cli import FailedServerScan
from sslyze.server_connectivity import ServerConnectivityInfo, ServerConnectivityError
from sslyze.ssl_settings import TlsWrappedProtocolEnum, ClientAuthenticationCredentials, HttpConnectTunnelingSettings
from sslyze.utils.ssl_connection import SSLConnection


class CommandLineParsingError(ValueError):

    PARSING_ERROR_FORMAT = u'  Command line error: {0}\n  Use -h for help.'

    def get_error_msg(self):
        return self.PARSING_ERROR_FORMAT.format(self)


class CommandLineServerStringParser(object):
    """Utility class to parse a 'host:port{ip}' string taken from the command line into a valid (host,ip, port) tuple.
    Supports IPV6 addresses.
    """

    SERVER_STRING_ERROR_BAD_PORT = u'Not a valid host:port'
    SERVER_STRING_ERROR_NO_IPV6 = u'IPv6 is not supported on this platform'

    @classmethod
    def parse_server_string(cls, server_str):
        # type: (Text) -> Tuple[Text, Text, int]
        # Extract ip from target
        if '{' in server_str and '}' in server_str:
            raw_target = server_str.split('{')
            raw_ip = raw_target[1]

            ip = raw_ip.replace('}', '')

            # Clean the target
            server_str = raw_target[0]
        else:
            ip = None

        # Look for ipv6 hint in target
        if '[' in server_str:
            (host, port) = cls._parse_ipv6_server_string(server_str)
        else:
            # Look for ipv6 hint in the ip
            if ip is not None and '[' in ip:
                (ip, port) = cls._parse_ipv6_server_string(ip)

            # Fallback to ipv4
            (host, port) = cls._parse_ipv4_server_string(server_str)

        return host, ip, port

    @classmethod
    def _parse_ipv4_server_string(cls, server_str):

        if ':' in server_str:
            host = (server_str.split(':'))[0]  # hostname or ipv4 address
            try:
                port = int((server_str.split(':'))[1])
            except:  # Port is not an int
                raise ServerConnectivityError(cls.SERVER_STRING_ERROR_BAD_PORT)
        else:
            host = server_str
            port = None

        return host, port

    @classmethod
    def _parse_ipv6_server_string(cls, server_str):

        if not socket.has_ipv6:
            raise ServerConnectivityError(cls.SERVER_STRING_ERROR_NO_IPV6)

        port = None
        target_split = (server_str.split(']'))
        ipv6_addr = target_split[0].split('[')[1]
        if ':' in target_split[1]:  # port was specified
            try:
                port = int(target_split[1].rsplit(':')[1])
            except:  # Port is not an int
                raise ServerConnectivityError(cls.SERVER_STRING_ERROR_BAD_PORT)
        return ipv6_addr, port


class CommandLineParser(object):

    # Defines what --regular means
    REGULAR_CMD = [u'sslv2', u'sslv3', u'tlsv1', u'tlsv1_1', u'tlsv1_2', u'reneg', u'resum', u'certinfo_basic',
                   u'http_get', u'hide_rejected_ciphers', u'compression', u'heartbleed', u'openssl_ccs', u'fallback']
    SSLYZE_USAGE = u'usage: %prog [options] target1.com target2.com:443 target3.com:443{ip} etc...'

    # StartTLS options
    START_TLS_PROTOCOLS = [u'smtp', u'xmpp', u'xmpp_server', u'pop3', u'ftp', u'imap', u'ldap', u'rdp', u'postgres',
                           u'auto']
    START_TLS_USAGE = u'StartTLS should be one of: {}. The \'auto\' option will cause SSLyze to deduce the protocol ' \
                      u'(ftp, imap, etc.) from the supplied port number, ' \
                      u'for each target servers.'.format(u' , '.join(START_TLS_PROTOCOLS))

    # Mapping of StartTls protocols and ports; useful for starttls=auto
    STARTTLS_PROTOCOL_DICT = {u'smtp': TlsWrappedProtocolEnum.STARTTLS_SMTP,
                              587: TlsWrappedProtocolEnum.STARTTLS_SMTP,
                              25: TlsWrappedProtocolEnum.STARTTLS_SMTP,
                              u'xmpp': TlsWrappedProtocolEnum.STARTTLS_XMPP,
                              5222: TlsWrappedProtocolEnum.STARTTLS_XMPP,
                              u'xmpp_server': TlsWrappedProtocolEnum.STARTTLS_XMPP_SERVER,
                              5269: TlsWrappedProtocolEnum.STARTTLS_XMPP_SERVER,
                              u'pop3': TlsWrappedProtocolEnum.STARTTLS_POP3,
                              109: TlsWrappedProtocolEnum.STARTTLS_POP3,
                              110: TlsWrappedProtocolEnum.STARTTLS_POP3,
                              u'imap': TlsWrappedProtocolEnum.STARTTLS_IMAP,
                              143: TlsWrappedProtocolEnum.STARTTLS_IMAP,
                              220: TlsWrappedProtocolEnum.STARTTLS_IMAP,
                              u'ftp': TlsWrappedProtocolEnum.STARTTLS_FTP,
                              21: TlsWrappedProtocolEnum.STARTTLS_FTP,
                              u'ldap': TlsWrappedProtocolEnum.STARTTLS_LDAP,
                              3268: TlsWrappedProtocolEnum.STARTTLS_LDAP,
                              389: TlsWrappedProtocolEnum.STARTTLS_LDAP,
                              u'rdp': TlsWrappedProtocolEnum.STARTTLS_RDP,
                              3389: TlsWrappedProtocolEnum.STARTTLS_RDP,
                              u'postgres': TlsWrappedProtocolEnum.STARTTLS_POSTGRES,
                              5432: TlsWrappedProtocolEnum.STARTTLS_POSTGRES}

    def __init__(self, available_plugins, sslyze_version):
        """Generate SSLyze's command line parser.
        """
        self._parser = OptionParser(version=sslyze_version, usage=self.SSLYZE_USAGE)

        # Add generic command line options to the parser
        self._add_default_options()

        # Add plugin-specific options to the parser
        self._add_plugin_options(available_plugins)

        # Add the --regular command line parameter as a shortcut if possible
        regular_help = u'Regular HTTPS scan; shortcut for --{}'.format(u' --'.join(self.REGULAR_CMD))
        self._parser.add_option(u'--regular', action=u'store_true', dest=None, help=regular_help)


    def parse_command_line(self):
        """Parses the command line used to launch SSLyze.
        """

        (args_command_list, args_target_list) = self._parser.parse_args()

        # Handle the --targets_in command line and fill args_target_list
        if args_command_list.targets_in:
            if args_target_list:
                raise CommandLineParsingError(u'Cannot use --targets_list and specify targets within the command line.')

            try:  # Read targets from a file
                with open(args_command_list.targets_in) as f:
                    for target in f.readlines():
                        if target.strip():  # Ignore empty lines
                            if not target.startswith(u'#'):  # Ignore comment lines
                                args_target_list.append(target.strip())
            except IOError:
                raise CommandLineParsingError(u'Can\'t read targets from input file \'{}.'.format(
                    args_command_list.targets_in))

        if not args_target_list:
            raise CommandLineParsingError(u'No targets to scan.')


        # Handle the --regular command line parameter as a shortcut
        if self._parser.has_option(u'--regular'):
            if getattr(args_command_list, u'regular'):
                setattr(args_command_list, u'regular', False)
                for cmd in self.REGULAR_CMD:
                    setattr(args_command_list, cmd, True)


        # Sanity checks on the command line options
        # Prevent --quiet and --xml_out -
        if args_command_list.xml_file and args_command_list.xml_file == '-' and args_command_list.quiet:
                raise CommandLineParsingError(u'Cannot use --quiet with --xml_out -.')

        # Prevent --quiet and --json_out -
        if args_command_list.json_file and args_command_list.json_file == '-' and args_command_list.quiet:
                raise CommandLineParsingError(u'Cannot use --quiet with --json_out -.')

        # Prevent --xml_out - and --json_out -
        if args_command_list.json_file and args_command_list.json_file == '-' \
                and args_command_list.xml_file and args_command_list.xml_file == '-':
                raise CommandLineParsingError(u'Cannot use --xml_out - with --json_out -.')


        # Sanity checks on the client cert options
        client_auth_creds = None
        if bool(args_command_list.cert) ^ bool(args_command_list.key):
            raise CommandLineParsingError(u'No private key or certificate file were given. See --cert and --key.')

        elif args_command_list.cert:
            # Private key formats
            if args_command_list.keyform == u'DER':
                key_type = OpenSslFileTypeEnum.ASN1
            elif args_command_list.keyform == u'PEM':
                key_type = OpenSslFileTypeEnum.PEM
            else:
                raise CommandLineParsingError(u'--keyform should be DER or PEM.')

            # Let's try to open the cert and key files
            try:
                client_auth_creds = ClientAuthenticationCredentials(args_command_list.cert,
                                                                    args_command_list.key,
                                                                    key_type,
                                                                    args_command_list.keypass)
            except ValueError as e:
                raise CommandLineParsingError(u'Invalid client authentication settings: {}.'.format(e[0]))


        # HTTP CONNECT proxy
        http_tunneling_settings = None
        if args_command_list.https_tunnel:
            try:
                http_tunneling_settings = HttpConnectTunnelingSettings.from_url(args_command_list.https_tunnel)
            except ValueError as e:
                raise CommandLineParsingError(u'Invalid proxy URL for --https_tunnel: {}.'.format(e[0]))


        # STARTTLS
        tls_wrapped_protocol = TlsWrappedProtocolEnum.PLAIN_TLS
        if args_command_list.starttls:
            if args_command_list.starttls not in self.START_TLS_PROTOCOLS:
                raise CommandLineParsingError(self.START_TLS_USAGE)
            else:
                # StartTLS was specified
                if args_command_list.starttls in self.STARTTLS_PROTOCOL_DICT.keys():
                    # Protocol was given in the command line
                    tls_wrapped_protocol = self.STARTTLS_PROTOCOL_DICT[args_command_list.starttls]


        # Number of connection retries
        if args_command_list.nb_retries < 1:
            raise CommandLineParsingError(u'Cannot have a number smaller than 1 for --nb_retries.')


        # Create the server connectivity info for each specifed servers
        # A limitation when using the command line is that only one client_auth_credentials and http_tunneling_settings
        # can be specified, for all the servers to scan
        good_server_list = []
        bad_server_list = []
        for server_string in args_target_list:
            # Support unicode domains
            server_string = server_string.decode('utf-8')
            try:
                hostname, ip_address, port = CommandLineServerStringParser.parse_server_string(server_string)
                server_info = ServerConnectivityInfo(
                    hostname=hostname,
                    port=port,
                    ip_address=ip_address,
                    tls_wrapped_protocol=tls_wrapped_protocol,
                    tls_server_name_indication=args_command_list.sni,
                    xmpp_to_hostname=args_command_list.xmpp_to,
                    client_auth_credentials=client_auth_creds,
                    http_tunneling_settings=http_tunneling_settings
                )
                # Keep the original server string to display it in the CLI output if there was a connection error
                server_info.server_string = server_string
                
                good_server_list.append(server_info)
            except ServerConnectivityError as e:
                # Will happen for example if the DNS lookup failed or the server string is malformed
                bad_server_list.append(FailedServerScan(server_string, e))
            except ValueError as e:
                # Will happen for example if xmpp_to is specified for a non-XMPP connection
                raise CommandLineParsingError(e[0])

        # Command line hacks
        # Handle --starttls=auto now that we parsed the server strings
        if args_command_list.starttls == u'auto':
            for server_info in good_server_list:
                # We use the port number to deduce the protocol
                if server_info.port in self.STARTTLS_PROTOCOL_DICT.keys():
                    server_info.tls_wrapped_protocol = self.STARTTLS_PROTOCOL_DICT[server_info.port]

        # Handle --http_get now that we parsed the server strings
        # Doing it here is hacky as the option is defined within PluginOpenSSLCipherSuites
        if args_command_list.http_get:
            for server_info in good_server_list:
                if server_info.port == 443:
                    server_info.tls_wrapped_protocol = TlsWrappedProtocolEnum.HTTPS

        return good_server_list, bad_server_list, args_command_list


    def _add_default_options(self):
        """Add default command line options to the parser.
        """

        # Client certificate options
        clientcert_group = OptionGroup(self._parser, u'Client certificate options', u'')
        clientcert_group.add_option(
            u'--cert',
            help=u'Client certificate chain filename. The certificates must be in PEM format and must be sorted '
                 u'starting with the subject\'s client certificate, followed by intermediate CA certificates if '
                 u'applicable.',
            dest=u'cert'
        )
        clientcert_group.add_option(
            u'--key',
            help=u'Client private key filename.',
            dest=u'key'
        )
        clientcert_group.add_option(
            u'--keyform',
            help=u'Client private key format. DER or PEM (default).',
            dest=u'keyform',
            default=u'PEM'
        )
        clientcert_group.add_option(
            u'--pass',
            help=u'Client private key passphrase.',
            dest=u'keypass',
            default=u''
        )
        self._parser.add_option_group(clientcert_group)

        # Input / output
        output_group = OptionGroup(self._parser, u'Input and output options', u'')
        # XML output
        output_group.add_option(
            u'--xml_out',
            help=u'Write the scan results as an XML document to the file XML_FILE. If XML_FILE is set to "-", the XML '
                 u'output will instead be printed to stdout.',
            dest=u'xml_file',
            default=None
        )
        # JSON output
        output_group.add_option(
            u'--json_out',
            help=u'Write the scan results as a JSON document to the file JSON_FILE. If JSON_FILE is set to "-", the '
                 u'JSON output will instead be printed to stdout. The resulting JSON file is a serialized version of '
                 u'the ScanResult objects described in SSLyze\'s Python API: the nodes and attributes will be the same. '
                 u'See https://nabla-c0d3.github.io/SSLyze/documentation/available-scan-commands.html for more details.',
            dest=u'json_file',
            default=None
        )
        # Read targets from input file
        output_group.add_option(
            u'--targets_in',
            help=u'Read the list of targets to scan from the file TARGETS_IN. It should contain one host:port per '
                 u'line.',
            dest=u'targets_in',
            default=None
        )
        # No text output
        output_group.add_option(
            u'--quiet',
            action=u'store_true',
            dest=u'quiet',
            help=u'Do not output anything to stdout; useful when using --xml_out or --json_out.'
        )
        self._parser.add_option_group(output_group)

        # Connectivity option group
        connect_group = OptionGroup(self._parser, u'Connectivity options', u'')
        # Timeout
        connect_group.add_option(
            u'--timeout',
            help=u'Set the timeout value in seconds used for every socket connection made to the target server(s). '
                 u'Default is {}s.'.format(str(SSLConnection.NETWORK_TIMEOUT)),
            type=u'int',
            dest=u'timeout',
            default=SSLConnection.NETWORK_TIMEOUT
        )
        # Control connection retry attempts
        connect_group.add_option(
            u'--nb_retries',
            help=u'Set the number retry attempts for all network connections initiated throughout the scan. Increase '
                 u'this value if you are getting a lot of timeout/connection errors when scanning a specific server. '
                 u'Decrease this value to increase the speed of the scans; results may however return connection '
                 u'errors. Default is {} connection attempts.'.format(str(SSLConnection.NETWORK_MAX_RETRIES)),
            type=u'int',
            dest=u'nb_retries',
            default=SSLConnection.NETWORK_MAX_RETRIES
        )
        # HTTP CONNECT Proxy
        connect_group.add_option(
            u'--https_tunnel',
            help=u'Tunnel all traffic to the target server(s) through an HTTP CONNECT proxy. HTTP_TUNNEL should be the '
                 u'proxy\'s URL: \'http://USER:PW@HOST:PORT/\'. For proxies requiring authentication, only Basic '
                 u'Authentication is supported.',
            dest=u'https_tunnel',
            default=None
        )
        # STARTTLS
        connect_group.add_option(
            u'--starttls',
            help=u'Perform a StartTLS handshake when connecting to the target server(s). '
                 u'{}'.format(self.START_TLS_USAGE),
            dest=u'starttls',
            default=None
        )
        connect_group.add_option(
            u'--xmpp_to',
            help=u'Optional setting for STARTTLS XMPP. XMPP_TO should be the hostname to be put in the \'to\' '
                 u'attribute of the XMPP stream. Default is the server\'s hostname.',
            dest=u'xmpp_to',
            default=None
        )
        # Server Name Indication
        connect_group.add_option(
            u'--sni',
            help=u'Use Server Name Indication to specify the hostname to connect to.  Will only affect TLS 1.0+ '
                 u'connections.',
            dest=u'sni',
            default=None
        )
        self._parser.add_option_group(connect_group)


    def _add_plugin_options(self, available_plugins):
        """Recovers the list of command line options implemented by the available plugins and adds them to the command
        line parser.
        """
        for plugin_class in available_plugins:
            # Add the current plugin's commands to the parser
            group = OptionGroup(self._parser, plugin_class.get_title(), plugin_class.get_description())
            for option in plugin_class.get_cli_option_group():
                group.add_option(option)
            self._parser.add_option_group(group)
