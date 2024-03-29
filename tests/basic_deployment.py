import subprocess
import amulet
import json
import time
from ceilometerclient.v2 import client as ceilclient

from charmhelpers.contrib.openstack.amulet.deployment import (
    OpenStackAmuletDeployment
)

from charmhelpers.contrib.openstack.amulet.utils import (
    OpenStackAmuletUtils,
    DEBUG,
    # ERROR
)

# Use DEBUG to turn on debug logging
u = OpenStackAmuletUtils(DEBUG)


class CeilometerBasicDeployment(OpenStackAmuletDeployment):
    """Amulet tests on a basic ceilometer deployment."""

    def __init__(self, series, openstack=None, source=None, stable=False):
        """Deploy the entire test environment."""
        super(CeilometerBasicDeployment, self).__init__(series, openstack,
                                                        source, stable)
        self._add_services()
        self._add_relations()
        self._configure_services()
        self._deploy()

        u.log.info('Waiting on extended status checks...')
        exclude_services = ['mysql', 'mongodb']
        self._auto_wait_for_status(exclude_services=exclude_services)

        self._initialize_tests()

    def _add_services(self):
        """Add services

           Add the services that we're testing, where ceilometer is local,
           and the rest of the service are from lp branches that are
           compatible with the local charm (e.g. stable or next).
           """
        this_service = {'name': 'ceilometer'}
        other_services = [{'name': 'mysql'},
                          {'name': 'rabbitmq-server'},
                          {'name': 'keystone'},
                          {'name': 'mongodb'},
                          {'name': 'glance'},  # to satisfy workload status
                          {'name': 'ceilometer-agent'},
                          {'name': 'nova-compute'}]
        super(CeilometerBasicDeployment, self)._add_services(this_service,
                                                             other_services)

    def _add_relations(self):
        """Add all of the relations for the services."""
        relations = {
            'ceilometer:shared-db': 'mongodb:database',
            'ceilometer:amqp': 'rabbitmq-server:amqp',
            'ceilometer:identity-service': 'keystone:identity-service',
            'ceilometer:identity-notifications': 'keystone:'
                                                 'identity-notifications',
            'keystone:shared-db': 'mysql:shared-db',
            'ceilometer:ceilometer-service': 'ceilometer-agent:'
                                             'ceilometer-service',
            'nova-compute:nova-ceilometer': 'ceilometer-agent:nova-ceilometer',
            'nova-compute:shared-db': 'mysql:shared-db',
            'nova-compute:amqp': 'rabbitmq-server:amqp',
            'glance:identity-service': 'keystone:identity-service',
            'glance:shared-db': 'mysql:shared-db',
            'glance:amqp': 'rabbitmq-server:amqp',
            'nova-compute:image-service': 'glance:image-service'
        }
        super(CeilometerBasicDeployment, self)._add_relations(relations)

    def _configure_services(self):
        """Configure all of the services."""
        keystone_config = {'admin-password': 'openstack',
                           'admin-token': 'ubuntutesting'}
        configs = {'keystone': keystone_config}
        super(CeilometerBasicDeployment, self)._configure_services(configs)

    def _get_token(self):
        return self.keystone.service_catalog.catalog['token']['id']

    def _initialize_tests(self):
        """Perform final initialization before tests get run."""
        # Access the sentries for inspecting service units
        self.ceil_sentry = self.d.sentry.unit['ceilometer/0']
        self.ceil_agent_sentry = self.d.sentry.unit['ceilometer-agent/0']
        self.mysql_sentry = self.d.sentry.unit['mysql/0']
        self.keystone_sentry = self.d.sentry.unit['keystone/0']
        self.rabbitmq_sentry = self.d.sentry.unit['rabbitmq-server/0']
        self.mongodb_sentry = self.d.sentry.unit['mongodb/0']
        self.nova_sentry = self.d.sentry.unit['nova-compute/0']
        u.log.debug('openstack release val: {}'.format(
            self._get_openstack_release()))
        u.log.debug('openstack release str: {}'.format(
            self._get_openstack_release_string()))

        # Authenticate admin with keystone endpoint
        self.keystone = u.authenticate_keystone_admin(self.keystone_sentry,
                                                      user='admin',
                                                      password='openstack',
                                                      tenant='admin')

        # Authenticate admin with ceilometer endpoint
        ep = self.keystone.service_catalog.url_for(service_type='metering',
                                                   endpoint_type='publicURL')
        self.ceil = ceilclient.Client(endpoint=ep, token=self._get_token)

    def _run_action(self, unit_id, action, *args):
        command = ["juju", "action", "do", "--format=json", unit_id, action]
        command.extend(args)
        print("Running command: %s\n" % " ".join(command))
        output = subprocess.check_output(command)
        output_json = output.decode(encoding="UTF-8")
        data = json.loads(output_json)
        action_id = data[u'Action queued with id']
        return action_id

    def _wait_on_action(self, action_id):
        command = ["juju", "action", "fetch", "--format=json", action_id]
        while True:
            try:
                output = subprocess.check_output(command)
            except Exception as e:
                print(e)
                return False
            output_json = output.decode(encoding="UTF-8")
            data = json.loads(output_json)
            if data[u"status"] == "completed":
                return True
            elif data[u"status"] == "failed":
                return False
            time.sleep(2)

    def test_100_services(self):
        """Verify the expected services are running on the corresponding
           service units."""
        u.log.debug('Checking system services on units...')

        ceilometer_svcs = [
            'ceilometer-agent-central',
            'ceilometer-collector',
            'ceilometer-api',
            'ceilometer-alarm-evaluator',
            'ceilometer-alarm-notifier',
            'ceilometer-agent-notification',
        ]
        service_names = {
            self.ceil_sentry: ceilometer_svcs,
            self.mysql_sentry: ['mysql'],
            self.keystone_sentry: ['keystone'],
            self.rabbitmq_sentry: ['rabbitmq-server'],
            self.mongodb_sentry: ['mongodb'],
        }

        ret = u.validate_services_by_name(service_names)
        if ret:
            amulet.raise_status(amulet.FAIL, msg=ret)

        u.log.debug('OK')

    def test_110_service_catalog(self):
        """Verify that the service catalog endpoint data is valid."""
        u.log.debug('Checking keystone service catalog data...')
        endpoint_check = {
            'adminURL': u.valid_url,
            'id': u.not_null,
            'region': 'RegionOne',
            'publicURL': u.valid_url,
            'internalURL': u.valid_url
        }
        expected = {
            'metering': [endpoint_check],
            'identity': [endpoint_check]
        }
        actual = self.keystone.service_catalog.get_endpoints()

        ret = u.validate_svc_catalog_endpoint_data(expected, actual)
        if ret:
            amulet.raise_status(amulet.FAIL, msg=ret)

        u.log.debug('OK')

    def test_112_keystone_api_endpoint(self):
        """Verify the ceilometer api endpoint data."""
        u.log.debug('Checking keystone api endpoint data...')
        endpoints = self.keystone.endpoints.list()
        u.log.debug(endpoints)
        internal_port = public_port = '5000'
        admin_port = '35357'
        expected = {'id': u.not_null,
                    'region': 'RegionOne',
                    'adminurl': u.valid_url,
                    'internalurl': u.valid_url,
                    'publicurl': u.valid_url,
                    'service_id': u.not_null}

        ret = u.validate_endpoint_data(endpoints, admin_port, internal_port,
                                       public_port, expected)
        if ret:
            message = 'Keystone endpoint: {}'.format(ret)
            amulet.raise_status(amulet.FAIL, msg=message)

        u.log.debug('OK')

    def test_114_ceilometer_api_endpoint(self):
        """Verify the ceilometer api endpoint data."""
        u.log.debug('Checking ceilometer api endpoint data...')
        endpoints = self.keystone.endpoints.list()
        u.log.debug(endpoints)
        admin_port = internal_port = public_port = '8777'
        expected = {'id': u.not_null,
                    'region': 'RegionOne',
                    'adminurl': u.valid_url,
                    'internalurl': u.valid_url,
                    'publicurl': u.valid_url,
                    'service_id': u.not_null}

        ret = u.validate_endpoint_data(endpoints, admin_port, internal_port,
                                       public_port, expected)
        if ret:
            message = 'Ceilometer endpoint: {}'.format(ret)
            amulet.raise_status(amulet.FAIL, msg=message)

        u.log.debug('OK')

    def test_200_ceilometer_identity_relation(self):
        """Verify the ceilometer to keystone identity-service relation data"""
        u.log.debug('Checking ceilometer to keystone identity-service '
                    'relation data...')
        unit = self.ceil_sentry
        relation = ['identity-service', 'keystone:identity-service']
        ceil_ip = unit.relation('identity-service',
                                'keystone:identity-service')['private-address']
        ceil_endpoint = "http://%s:8777" % (ceil_ip)

        expected = {
            'admin_url': ceil_endpoint,
            'internal_url': ceil_endpoint,
            'private-address': ceil_ip,
            'public_url': ceil_endpoint,
            'region': 'RegionOne',
            'requested_roles': 'ResellerAdmin',
            'service': 'ceilometer',
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('ceilometer identity-service', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

        u.log.debug('OK')

    def test_201_keystone_ceilometer_identity_relation(self):
        """Verify the keystone to ceilometer identity-service relation data"""
        u.log.debug('Checking keystone:ceilometer identity relation data...')
        unit = self.keystone_sentry
        relation = ['identity-service', 'ceilometer:identity-service']
        id_relation = unit.relation('identity-service',
                                    'ceilometer:identity-service')
        id_ip = id_relation['private-address']
        expected = {
            'admin_token': 'ubuntutesting',
            'auth_host': id_ip,
            'auth_port': "35357",
            'auth_protocol': 'http',
            'private-address': id_ip,
            'service_host': id_ip,
            'service_password': u.not_null,
            'service_port': "5000",
            'service_protocol': 'http',
            'service_tenant': 'services',
            'service_tenant_id': u.not_null,
            'service_username': 'ceilometer',
        }
        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('keystone identity-service', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

        u.log.debug('OK')

    def test_202_keystone_ceilometer_identity_notes_relation(self):
        """Verify ceilometer to keystone identity-notifications relation"""
        u.log.debug('Checking keystone:ceilometer '
                    'identity-notifications relation data...')

        # Relation data may vary depending on timing of hooks and relations.
        # May be glance- or keystone- or another endpoint-changed value, so
        # check that at least one ???-endpoint-changed value exists.
        unit = self.keystone_sentry
        relation_data = unit.relation('identity-service',
                                      'ceilometer:identity-notifications')

        expected = '-endpoint-changed'
        found = 0
        for key in relation_data.keys():
            if expected in key and relation_data[key]:
                found += 1
                u.log.debug('{}: {}'.format(key, relation_data[key]))

        if not found:
            message = ('keystone:ceilometer identity-notification relation '
                       'error\n expected something like: {}\n actual: '
                       '{}'.format(expected, relation_data))
            amulet.raise_status(amulet.FAIL, msg=message)

        u.log.debug('OK')

    def test_203_ceilometer_amqp_relation(self):
        """Verify the ceilometer to rabbitmq-server amqp relation data"""
        u.log.debug('Checking ceilometer:rabbitmq amqp relation data...')
        unit = self.ceil_sentry
        relation = ['amqp', 'rabbitmq-server:amqp']
        expected = {
            'username': 'ceilometer',
            'private-address': u.valid_ip,
            'vhost': 'openstack'
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('ceilometer amqp', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

        u.log.debug('OK')

    def test_204_amqp_ceilometer_relation(self):
        """Verify the rabbitmq-server to ceilometer amqp relation data"""
        u.log.debug('Checking rabbitmq:ceilometer amqp relation data...')
        unit = self.rabbitmq_sentry
        relation = ['amqp', 'ceilometer:amqp']
        expected = {
            'hostname': u.valid_ip,
            'private-address': u.valid_ip,
            'password': u.not_null,
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('rabbitmq amqp', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

        u.log.debug('OK')

    def test_205_ceilometer_to_mongodb_relation(self):
        """Verify the ceilometer to mongodb relation data"""
        u.log.debug('Checking ceilometer:mongodb relation data...')
        unit = self.ceil_sentry
        relation = ['shared-db', 'mongodb:database']
        expected = {
            'ceilometer_database': 'ceilometer',
            'private-address': u.valid_ip,
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('ceilometer shared-db', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

        u.log.debug('OK')

    def test_206_mongodb_to_ceilometer_relation(self):
        """Verify the mongodb to ceilometer relation data"""
        u.log.debug('Checking mongodb:ceilometer relation data...')
        unit = self.mongodb_sentry
        relation = ['database', 'ceilometer:shared-db']
        expected = {
            'hostname': u.valid_ip,
            'port': '27017',
            'private-address': u.valid_ip,
            'type': 'database',
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('mongodb database', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

        u.log.debug('OK')

    def test_207_ceilometer_ceilometer_agent_relation(self):
        """Verify the ceilometer to ceilometer-agent relation data"""
        u.log.debug('Checking ceilometer:ceilometer-agent relation data...')
        unit = self.ceil_sentry
        relation = ['ceilometer-service',
                    'ceilometer-agent:ceilometer-service']
        expected = {
            'rabbitmq_user': 'ceilometer',
            'verbose': 'False',
            'rabbitmq_host': u.valid_ip,
            'service_ports': "{'ceilometer_api': [8777, 8767]}",
            'use_syslog': 'False',
            'metering_secret': u.not_null,
            'rabbitmq_virtual_host': 'openstack',
            'db_port': '27017',
            'private-address': u.valid_ip,
            'db_name': 'ceilometer',
            'db_host': u.valid_ip,
            'debug': 'False',
            'rabbitmq_password': u.not_null,
            'port': '8767'
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('ceilometer-service', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

        u.log.debug('OK')

    def test_208_ceilometer_agent_ceilometer_relation(self):
        """Verify the ceilometer-agent to ceilometer relation data"""
        u.log.debug('Checking ceilometer-agent:ceilometer relation data...')
        unit = self.ceil_agent_sentry
        relation = ['ceilometer-service', 'ceilometer:ceilometer-service']
        expected = {'private-address': u.valid_ip}

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('ceilometer-service', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

        u.log.debug('OK')

    def test_209_nova_compute_ceilometer_agent_relation(self):
        """Verify the nova-compute to ceilometer relation data"""
        u.log.debug('Checking nova-compute:ceilometer relation data...')
        unit = self.nova_sentry
        relation = ['nova-ceilometer', 'ceilometer-agent:nova-ceilometer']
        expected = {'private-address': u.valid_ip}

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('ceilometer-service', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_210_ceilometer_agent_nova_compute_relation(self):
        """Verify the ceilometer to nova-compute relation data"""
        u.log.debug('Checking ceilometer:nova-compute relation data...')
        unit = self.ceil_agent_sentry
        relation = ['nova-ceilometer', 'nova-compute:nova-ceilometer']
        sub = ('{"nova": {"/etc/nova/nova.conf": {"sections": {"DEFAULT": '
               '[["instance_usage_audit", "True"], '
               '["instance_usage_audit_period", "hour"], '
               '["notify_on_state_change", "vm_and_task_state"], '
               '["notification_driver", "ceilometer.compute.nova_notifier"], '
               '["notification_driver", '
               '"nova.openstack.common.notifier.rpc_notifier"]]}}}}')
        expected = {
            'subordinate_configuration': sub,
            'private-address': u.valid_ip
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('ceilometer-service', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

        u.log.debug('OK')

    def test_300_ceilometer_config(self):
        """Verify the data in the ceilometer config file."""
        u.log.debug('Checking ceilometer config file data...')
        unit = self.ceil_sentry
        rmq_rel = self.rabbitmq_sentry.relation('amqp',
                                                'ceilometer:amqp')
        ks_rel = self.keystone_sentry.relation('identity-service',
                                               'ceilometer:identity-service')
        auth_uri = '%s://%s:%s/' % (ks_rel['service_protocol'],
                                    ks_rel['service_host'],
                                    ks_rel['service_port'])
        db_relation = self.mongodb_sentry.relation('database',
                                                   'ceilometer:shared-db')
        db_conn = 'mongodb://%s:%s/ceilometer' % (db_relation['hostname'],
                                                  db_relation['port'])
        conf = '/etc/ceilometer/ceilometer.conf'
        expected = {
            'DEFAULT': {
                'verbose': 'False',
                'debug': 'False',
                'use_syslog': 'False',
                'rabbit_userid': 'ceilometer',
                'rabbit_virtual_host': 'openstack',
                'rabbit_password': rmq_rel['password'],
                'rabbit_host': rmq_rel['hostname'],
            },
            'api': {
                'port': '8767',
            },
            'service_credentials': {
                'os_auth_url': auth_uri + 'v2.0',
                'os_tenant_name': 'services',
                'os_username': 'ceilometer',
                'os_password': ks_rel['service_password'],
            },
            'database': {
                'connection': db_conn,
            },
            'keystone_authtoken': {
                'auth_uri': auth_uri,
                'auth_host': ks_rel['auth_host'],
                'auth_port': ks_rel['auth_port'],
                'auth_protocol': ks_rel['auth_protocol'],
                'admin_tenant_name': 'services',
                'admin_user': 'ceilometer',
                'admin_password': ks_rel['service_password'],
            },
        }

        for section, pairs in expected.iteritems():
            ret = u.validate_config_data(unit, conf, section, pairs)
            if ret:
                message = "ceilometer config error: {}".format(ret)
                amulet.raise_status(amulet.FAIL, msg=message)

        u.log.debug('OK')

    def test_301_nova_config(self):
        """Verify data in the nova compute nova config file"""
        u.log.debug('Checking nova compute config file...')
        unit = self.nova_sentry
        conf = '/etc/nova/nova.conf'
        expected = {
            'DEFAULT': {
                'verbose': 'False',
                'debug': 'False',
                'use_syslog': 'False',
                'my_ip': u.valid_ip,
                'dhcpbridge_flagfile': '/etc/nova/nova.conf',
                'dhcpbridge': '/usr/bin/nova-dhcpbridge',
                'logdir': '/var/log/nova',
                'state_path': '/var/lib/nova',
                'api_paste_config': '/etc/nova/api-paste.ini',
                'enabled_apis': 'ec2,osapi_compute,metadata',
                'auth_strategy': 'keystone',
                'instance_usage_audit': 'True',
                'instance_usage_audit_period': 'hour',
                'notify_on_state_change': 'vm_and_task_state',
            }
        }

        # NOTE(beisner): notification_driver is not checked like the
        # others, as configparser does not support duplicate config
        # options, and dicts cant have duplicate keys.
        for section, pairs in expected.iteritems():
            ret = u.validate_config_data(unit, conf, section, pairs)
            if ret:
                message = "ceilometer config error: {}".format(ret)
                amulet.raise_status(amulet.FAIL, msg=message)

        # Check notification_driver existence via simple grep cmd
        lines = [('notification_driver = '
                  'ceilometer.compute.nova_notifier'),
                 ('notification_driver = '
                  'nova.openstack.common.notifier.rpc_notifier')]

        sentry_units = [unit]
        cmds = []
        for line in lines:
            cmds.append('grep "{}" {}'.format(line, conf))

        ret = u.check_commands_on_units(cmds, sentry_units)
        if ret:
            amulet.raise_status(amulet.FAIL, msg=ret)

        u.log.debug('OK')

    def test_302_nova_ceilometer_config(self):
        """Verify data in the ceilometer config file on the
        nova-compute (ceilometer-agent) unit."""
        u.log.debug('Checking nova ceilometer config file...')
        unit = self.nova_sentry
        conf = '/etc/ceilometer/ceilometer.conf'
        expected = {
            'DEFAULT': {
                'logdir': '/var/log/ceilometer'
            },
            'database': {
                'backend': 'sqlalchemy',
                'connection': 'sqlite:////var/lib/ceilometer/$sqlite_db'
            }
        }

        for section, pairs in expected.iteritems():
            ret = u.validate_config_data(unit, conf, section, pairs)
            if ret:
                message = "ceilometer config error: {}".format(ret)
                amulet.raise_status(amulet.FAIL, msg=message)

        u.log.debug('OK')

    def test_400_api_connection(self):
        """Simple api calls to check service is up and responding"""
        u.log.debug('Checking api functionality...')
        assert(self.ceil.samples.list() == [])
        assert(self.ceil.meters.list() == [])
        u.log.debug('OK')

    # NOTE(beisner): need to add more functional tests

    def test_900_restart_on_config_change(self):
        """Verify that the specified services are restarted when the config
           is changed.
           """
        sentry = self.ceil_sentry
        juju_service = 'ceilometer'

        # Expected default and alternate values
        set_default = {'debug': 'False'}
        set_alternate = {'debug': 'True'}

        # Services which are expected to restart upon config change,
        # and corresponding config files affected by the change
        conf_file = '/etc/ceilometer/ceilometer.conf'
        services = {
            'ceilometer-collector': conf_file,
            'ceilometer-api': conf_file,
            'ceilometer-alarm-evaluator': conf_file,
            'ceilometer-alarm-notifier': conf_file,
            'ceilometer-agent-notification': conf_file,
        }

        if self._get_openstack_release() == self.trusty_liberty or \
                self._get_openstack_release() >= self.wily_liberty:
            # Liberty and later
            services['ceilometer-polling'] = conf_file
        else:
            # Juno and earlier
            services['ceilometer-agent-central'] = conf_file

        # Make config change, check for service restarts
        u.log.debug('Making config change on {}...'.format(juju_service))
        mtime = u.get_sentry_time(sentry)
        self.d.configure(juju_service, set_alternate)

        sleep_time = 40
        for s, conf_file in services.iteritems():
            u.log.debug("Checking that service restarted: {}".format(s))
            if not u.validate_service_config_changed(sentry, mtime, s,
                                                     conf_file,
                                                     retry_count=4,
                                                     retry_sleep_time=20,
                                                     sleep_time=sleep_time):
                self.d.configure(juju_service, set_default)
                msg = "service {} didn't restart after config change".format(s)
                amulet.raise_status(amulet.FAIL, msg=msg)
            sleep_time = 0

        self.d.configure(juju_service, set_default)
        u.log.debug('OK')

    def test_910_pause_and_resume(self):
        """The services can be paused and resumed. """
        u.log.debug('Checking pause and resume actions...')
        unit_name = "ceilometer/0"
        unit = self.d.sentry.unit[unit_name]

        assert u.status_get(unit)[0] == "active"

        action_id = self._run_action(unit_name, "pause")
        assert self._wait_on_action(action_id), "Pause action failed."
        assert u.status_get(unit)[0] == "maintenance"

        action_id = self._run_action(unit_name, "resume")
        assert self._wait_on_action(action_id), "Resume action failed."
        assert u.status_get(unit)[0] == "active"
        u.log.debug('OK')
