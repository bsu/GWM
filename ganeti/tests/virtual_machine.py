# Copyright (C) 2010 Oregon State University et al.
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301,
# USA.


from datetime import datetime
import json

from django.conf import settings
from django.contrib.auth.models import User, Group
from django.core.urlresolvers import reverse
from django.db.utils import IntegrityError
from django.test import TestCase
from django.test.client import Client

from django_test_tools.views import ViewTestMixin
from django_test_tools.users import UserTestMixin
from ganeti.models import VirtualMachineTemplate
from object_permissions import grant, get_user_perms

from util import client
from ganeti.tests.rapi_proxy import RapiProxy, INSTANCE, INFO, JOB, \
    JOB_RUNNING, JOB_DELETE_SUCCESS, OPERATING_SYSTEMS
from ganeti import models, constants 
from ganeti.forms.virtual_machine import NewVirtualMachineForm
from ganeti.utilities import os_prettify, cluster_os_list

VirtualMachine = models.VirtualMachine
Cluster = models.Cluster
Node = models.Node
ClusterUser = models.ClusterUser
Job = models.Job
SSHKey = models.SSHKey

__all__ = (
    'TestVirtualMachineModel',
    'TestVirtualMachineViews',
    "TestVirtualMachineHelpers",
    'TestNewVirtualMachineForm',
    'VirtualMachineTestCaseMixin',
)

class VirtualMachineTestCaseMixin():
    def create_virtual_machine(self, cluster=None, hostname='vm1.osuosl.bak'):
        cluster = cluster if cluster else Cluster(hostname='test.osuosl.bak', slug='OSL_TEST', username='foo', password='bar')
        cluster.save()
        cluster.sync_nodes()
        vm = VirtualMachine(cluster=cluster, hostname=hostname)
        vm.save()
        return vm, cluster


class TestVirtualMachineModel(TestCase, VirtualMachineTestCaseMixin):

    def setUp(self):
        models.client.GanetiRapiClient = RapiProxy

    def tearDown(self):
        Job.objects.all().delete()
        VirtualMachine.objects.all().delete()
        Node.objects.all().delete()
        Cluster.objects.all().delete()
        User.objects.all().delete()
        Group.objects.all().delete()
        ClusterUser.objects.all().delete()

    def test_trivial(self):
        """
        Test the test case's setUp().
        """

        pass

    def test_instantiate(self):
        VirtualMachine()

    def test_non_trivial(self):
        """
        Test instantiating a VirtualMachine with extra parameters
        """
        # Define cluster for use
        vm_hostname='vm.test.org'
        cluster = Cluster(hostname='test.osuosl.bak', slug='OSL_TEST')
        cluster.save()
        owner = ClusterUser(id=32, name='foobar')
        
        # Cluster
        vm = VirtualMachine(cluster=cluster, hostname=vm_hostname)
        vm.save()
        self.assertTrue(vm.id)
        self.assertEqual('vm.test.org', vm.hostname)
        self.assertFalse(vm.error)
        vm.delete()
        
        # Multiple
        vm = VirtualMachine(cluster=cluster, hostname=vm_hostname,
                            virtual_cpus=3, ram=512, disk_size=5120,
                            owner=owner)
        vm.save()
        self.assertTrue(vm.id)
        self.assertEqual('vm.test.org', vm.hostname)
        self.assertEqual(512, vm.ram)
        self.assertEqual(5120, vm.disk_size)
        self.assertEqual('foobar', vm.owner.name)
        self.assertFalse(vm.error)
        
        # test unique constraints
        vm = VirtualMachine(cluster=cluster, hostname=vm_hostname)
        self.assertRaises(IntegrityError, vm.save)

    def test_save(self):
        """
        Test saving a VirtualMachine
        
        Verify:
            * VirtualMachine can be saved
            * VirtualMachine can be loaded
            * Hash is copied from cluster
        """
        vm, cluster = self.create_virtual_machine()
        self.assert_(vm.id)
        self.assertFalse(vm.error)
        self.assertEqual(vm.cluster_hash, cluster.hash)
        
        vm = VirtualMachine.objects.get(id=vm.id)
        self.assert_(vm.info)
        self.assertFalse(vm.error)
    
    def test_hash_update(self):
        """
        When cluster is saved hash for its VirtualMachines should be updated
        """
        vm0, cluster = self.create_virtual_machine()
        vm1, cluster = self.create_virtual_machine(cluster, 'test2.osuosl.bak')
        
        self.assertEqual(vm0.cluster_hash, cluster.hash)
        self.assertEqual(vm1.cluster_hash, cluster.hash)
        
        # change cluster's hash
        cluster.hostname = 'SomethingDifferent'        
        cluster.save()
        vm0 = VirtualMachine.objects.get(pk=vm0.id)
        vm1 = VirtualMachine.objects.get(pk=vm1.id)
        self.assertEqual(vm0.cluster_hash, cluster.hash, 'VirtualMachine does not have updated cache')
        self.assertEqual(vm1.cluster_hash, cluster.hash, 'VirtualMachine does not have updated cache')
    
    def test_parse_info(self):
        """
        Test parsing values from cached info
        
        Verifies:
            * mtime and ctime are parsed
            * ram, virtual_cpus, and disksize are parsed
        """
        vm, cluster = self.create_virtual_machine()
        vm.info = INSTANCE
        
        self.assertEqual(vm.ctime, datetime.fromtimestamp(1285799513.4741000))
        self.assertEqual(vm.mtime, datetime.fromtimestamp(1285883187.8692000))
        self.assertEqual(vm.ram, 512)
        self.assertEqual(vm.virtual_cpus, 2)
        self.assertEqual(vm.disk_size, 5120)

    def test_update_owner_tag(self):
        """
        Test changing owner
        """
        vm, cluster = self.create_virtual_machine()

        owner0 = ClusterUser(id=74, name='owner0')
        owner1 = ClusterUser(id=21, name='owner1')
        owner0.save()
        owner1.save()
        
        # no owner
        vm.refresh()
        self.assertEqual([], vm.info['tags'])
        
        # setting owner
        vm.owner = owner0
        vm.save()
        self.assertEqual(['%s%s' % (constants.OWNER_TAG, owner0.id)], vm.info['tags'])
        
        # changing owner
        vm.owner = owner1
        vm.save()
        self.assertEqual(['%s%s' % (constants.OWNER_TAG, owner1.id)], vm.info['tags'])
        
        # setting owner to none
        vm.owner = None
        vm.save()
        self.assertEqual([], vm.info['tags'])

    def test_start(self):
        """
        Test VirtualMachine.start()
        
        Verifies:
            * job is created
            * cache is disabled while job is running
            * cache is reenabled when job is finished
        """
        vm, cluster = self.create_virtual_machine()
        vm.rapi.GetJobStatus.response = JOB_RUNNING
        
        # reboot enables ignore_cache flag
        job_id = vm.startup().id
        vm = VirtualMachine.objects.get(id=vm.id)
        self.assert_(Job.objects.filter(id=job_id).exists())
        self.assert_(vm.ignore_cache)
        self.assert_(vm.last_job_id)
        
        # finished job resets ignore_cache flag
        vm.rapi.GetJobStatus.response = JOB
        vm = VirtualMachine.objects.get(id=vm.id)
        self.assertFalse(vm.ignore_cache)
        self.assertFalse(vm.last_job_id)
        self.assert_(Job.objects.get(id=job_id).finished)

    def test_stop(self):
        """
        Test VirtualMachine.stop()
        
        Verifies:
            * job is created
            * cache is disabled while job is running
            * cache is reenabled when job is finished
        """
        vm, cluster = self.create_virtual_machine()
        vm.rapi.GetJobStatus.response = JOB_RUNNING
        
        # reboot enables ignore_cache flag
        job_id = vm.shutdown().id
        self.assert_(Job.objects.filter(id=job_id).exists())
        vm = VirtualMachine.objects.get(id=vm.id)
        self.assert_(vm.ignore_cache)
        self.assert_(vm.last_job_id)
        self.assert_(Job.objects.filter(id=job_id).values()[0]['ignore_cache'])
        
        # finished job resets ignore_cache flag
        vm.rapi.GetJobStatus.response = JOB
        vm = VirtualMachine.objects.get(id=vm.id)
        self.assertFalse(vm.ignore_cache)
        self.assertFalse(vm.last_job_id)
        self.assertFalse(Job.objects.filter(id=job_id).values()[0]['ignore_cache'])
        self.assert_(Job.objects.get(id=job_id).finished)

    def test_reboot(self):
        """
        Test vm.reboot()
        
        Verifies:
            * job is created
            * cache is disabled while job is running
            * cache is reenabled when job is finished
        """
        vm, cluster = self.create_virtual_machine()
        vm.rapi.GetJobStatus.response = JOB_RUNNING
        
        # reboot enables ignore_cache flag
        job_id = vm.reboot().id
        self.assert_(Job.objects.filter(id=job_id).exists())
        vm = VirtualMachine.objects.get(id=vm.id)
        self.assert_(vm.ignore_cache)
        self.assert_(vm.last_job_id)
        self.assert_(Job.objects.filter(id=job_id).values()[0]['ignore_cache'])
        
        # finished job resets ignore_cache flag
        vm.rapi.GetJobStatus.response = JOB
        self.assert_(Job.objects.filter(id=job_id).exists())
        vm = VirtualMachine.objects.get(id=vm.id)
        self.assertFalse(vm.ignore_cache)
        self.assertFalse(vm.last_job_id)
        self.assertFalse(Job.objects.filter(id=job_id).values()[0]['ignore_cache'])
        self.assert_(Job.objects.get(id=job_id).finished)
    
    def test_load_pending_delete(self):
        """
        Tests loading a VM that has a pending delete
        
        Verifies:
            * The job is still running so the VM will be loaded
        """
        vm, cluster = self.create_virtual_machine()
        vm.rapi.GetJobStatus.response = JOB_RUNNING
        vm.refresh()
        vm.ignore_cache = True
        vm.pending_delete = True
        vm.last_job = Job.objects.create(job_id=1, obj=vm, cluster_id=vm.cluster_id)
        vm.save()
        
        # Test loading vm, job is running so it should not be deleted yet 
        vm = VirtualMachine.objects.get(pk=vm.pk)
        self.assert_(vm.id)
        self.assert_(vm.pending_delete)
        self.assertFalse(vm.deleted)
    
    def test_load_deleted(self): 
        """
        Tests loading a VM that has a pending delete
        
        Verifies:
            * The Job is finished.  It will load the VM but it will be deleted
            and marked as such.
        """
        vm, cluster = self.create_virtual_machine()
        vm.rapi.GetJobStatus.response = JOB_RUNNING
        vm.refresh()
        vm.ignore_cache = True
        vm.pending_delete = True
        vm.last_job = Job.objects.create(job_id=1, obj=vm, cluster_id=vm.cluster_id)
        vm.save()
        
        # Test loading vm, delete job is finished
        vm.rapi.GetJobStatus.response = JOB_DELETE_SUCCESS
        vm = VirtualMachine.objects.get(pk=vm.pk)
        self.assertFalse(vm.id)
        self.assert_(vm.pending_delete)
        self.assert_(vm.deleted)
        self.assertFalse(VirtualMachine.objects.filter(pk=vm.pk).exists())
    
class TestVirtualMachineViews(TestCase, VirtualMachineTestCaseMixin, ViewTestMixin, UserTestMixin):
    """
    Tests for views showing virtual machines
    """
    
    def setUp(self):
        self.tearDown()

        models.client.GanetiRapiClient = RapiProxy
        vm, cluster = self.create_virtual_machine()

        g = globals()
        self.create_standard_users(g)
        self.create_users([
              ('user',{'id':69}),
              ('user1',{'id':88}),
              ('vm_admin',{'id':77}),
              ('vm_modify',{'id':75}),
              ('cluster_migrate',{'id':78}),
              ('cluster_admin',{'id':99}),
        ], g)

        vm_admin.grant('admin', vm)
        vm_modify.grant('modify', vm)
        cluster_migrate.grant('migrate', cluster)
        cluster_admin.grant('admin', cluster)
        

        group = Group(id=42, name='testing_group')
        group.save()

        g['users'] = [superuser, vm_admin]
        g['vm'] = vm
        g['cluster'] = cluster
        g['c'] = Client()
        g['group'] = group

    def tearDown(self):
        VirtualMachineTemplate.objects.all().delete()
        Job.objects.all().delete()
        User.objects.all().delete()
        Group.objects.all().delete()
        VirtualMachine.objects.all().delete()
        Node.objects.all().delete()
        Cluster.objects.all().delete()
    
    def validate_get(self, url, args, template):
        self.assert_standard_fails(url, args)
        self.assert_200(url, args, [superuser, vm_admin], template=template)
    
    def validate_get_configurable(self, url, args, template=None, mimetype=False, perms=[]):
        """
        More configurable version of validate_get.
        Additional arguments (only if set) affects only authorized user test.

        @template: used template
        @mimetype: returned mimetype
        @perms:    set of perms granted on authorized user

        @return    response content
        """
        self.assert_standard_fails(url, args)
        
        # authorized user (perm)
        if perms:
            user.set_perms(perms, vm)
        self.assert_200(url, args, [superuser, user], mime=mimetype, template=template)

    
    def test_view_list(self):
        """
        Test listing all virtual machines
        """
        url = '/vms/'
        
        user2 = User(id=28, username='tester2', is_superuser=True)
        user2.set_password('secret')
        user2.save()
        
        # setup vms and perms
        vm1, cluster1 = self.create_virtual_machine(cluster, 'test1')
        vm2, cluster1 = self.create_virtual_machine(cluster, 'test2')
        vm3, cluster1 = self.create_virtual_machine(cluster, 'test3')
        user1.grant('admin', vm)
        user1.grant('admin', vm1)
        
        # anonymous user
        response = c.get(url, follow=True)
        self.assertEqual(200, response.status_code)
        self.assertTemplateUsed(response, 'registration/login.html')
        
        # user with perms on no virtual machines
        self.assert_(c.login(username=user.username, password='secret'))
        response = c.get(url)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/list.html')
        vms = response.context['vms'].object_list
        self.assertFalse(vms)
        
        # user with some perms
        self.assert_(c.login(username=user1.username, password='secret'))
        response = c.get(url)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/list.html')
        vms = response.context['vms'].object_list
        self.assert_(vm in vms)
        self.assert_(vm1 in vms)
        self.assertEqual(2, len(vms))
        
        # authorized (superuser)
        self.assert_(c.login(username=user2.username, password='secret'))
        response = c.get(url)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/list.html')
        vms = response.context['vms'].object_list
        self.assert_(vm in vms)
        self.assert_(vm1 in vms)
        self.assert_(vm2 in vms)
        self.assert_(vm3 in vms)
        self.assertEqual(4, len(vms))
    
    def test_view_detail(self):
        """
        Test showing virtual machine details
        """
        url = '/cluster/%s/%s/'
        args = (cluster.slug, vm.hostname)

        self.assert_standard_fails(url, args)
        self.assert_200(url, args, [superuser, vm_admin, cluster_admin], template='virtual_machine/detail.html')
    
    def validate_post_only_url(self, url, args=None, data=dict(), users=None, get_allowed=False):
        """
        generic function for testing urls that post with no data
        """
        vm = globals()['vm']
        args = args if args else (cluster.slug, vm.hostname)
        users = users if users else [superuser, vm_admin, cluster_admin]
        self.assert_standard_fails(url, args)

        def tests(user, response):
            content = json.loads(response.content)
            self.assertEqual('1', content['id'])
            VirtualMachine.objects.all().update(last_job=None)
            Job.objects.all().delete()

        self.assert_200(url, args, users, data=data, tests=tests, \
                        mime='application/json', method='post')

        # error while issuing reboot command
        def tests(user, response):
            content = json.loads(response.content)
            text = content['__all__'][0]
            self.assertEqual(msg, text)
            vm.rapi.error = None
        msg = "SIMULATING_AN_ERROR"
        vm.rapi.error = client.GanetiApiError(msg)
        self.assert_200(url, args, [superuser], data=data, mime='application/json', method='post', tests=tests)

        # invalid method
        if not get_allowed:
            self.assertTrue(c.login(username=superuser.username, password='secret'))
            response = c.get(url % args, data)
            self.assertEqual(405, response.status_code)
    
    def test_view_startup(self):
        """
        Test starting a virtual machine
        """
        self.validate_post_only_url('/cluster/%s/%s/startup')
    
    def test_view_startup_overquota(self):
        """
        Test starting a virtual machine that would cause the owner to exceed quota
        """
        vm = globals()['vm']
        args = (cluster.slug, vm.hostname)
        url = '/cluster/%s/%s/startup'

        # authorized (permission)
        self.assert_(c.login(username=user.username, password='secret'))

        grant(user, 'admin', vm)
        cluster.set_quota(user.get_profile(), dict(ram=10, disk=2000, virtual_cpus=10))
        vm.owner_id = user.get_profile().id
        vm.ram = 128
        vm.virtual_cpus = 1
        vm.save()

        response = c.post(url % args)
        self.assertEqual(200, response.status_code)
        self.assertEqual('application/json', response['content-type'])
        self.assert_('Owner does not have enough RAM' in response.content)
        user.revoke('admin', vm)
        VirtualMachine.objects.all().update(last_job=None)
        Job.objects.all().delete()        

        # restore values
        cluster.set_quota(user.get_profile(), dict(ram=10, disk=2000, virtual_cpus=10))
        vm.owner_id = None
        vm.ram = -1
        vm.virtual_cpus = -1

    def test_view_shutdown(self):
        """
        Test shutting down a virtual machine
        """
        self.validate_post_only_url('/cluster/%s/%s/shutdown')
    
    def test_view_reboot(self):
        """
        Test rebooting a virtual machine
        """
        self.validate_post_only_url('/cluster/%s/%s/reboot')

    def test_view_migrate(self):
        """
        Tests migrating a virtual machine
        """
        url = '/cluster/%s/%s/migrate'
        args = (cluster.slug, vm.hostname)
        template='virtual_machine/migrate.html'
        authorized = [superuser, cluster_admin, cluster_migrate]

        # get
        self.assert_standard_fails(url, args)
        self.assert_200(url, args, authorized, template=template)

        # post
        data = {'mode':'live'}
        self.validate_post_only_url(url, args, data, users=authorized, get_allowed=True)
        
    def test_view_ssh_keys(self):
        """
        Test getting SSH keys belonging to users, who have admin permission on
        specified virtual machine
        """
        # second virtual machine created
        vm1, cluster1 = self.create_virtual_machine(cluster, 'vm2.osuosl.bak')

        # grant admin permission to first user
        user.grant("admin", vm)
        
        # add some keys
        key = SSHKey(key="ssh-rsa test test@test", user=user)
        key.save()
        key1 = SSHKey(key="ssh-dsa test asd@asd", user=user)
        key1.save()

        # get API key
        import settings, json
        key = settings.WEB_MGR_API_KEY

        # forbidden
        response = c.get( reverse("instance-keys", args=[cluster.slug, vm.hostname, key+"a"]))
        self.assertEqual( 403, response.status_code )

        # not found
        response = c.get( reverse("instance-keys", args=[cluster.slug, vm.hostname+"a", key]))
        self.assertEqual( 404, response.status_code )
        response = c.get( reverse("instance-keys", args=[cluster.slug+"a", vm.hostname, key]))
        self.assertEqual( 404, response.status_code )

        # vm with users who have admin perms
        response = c.get( reverse("instance-keys", args=[cluster.slug, vm.hostname, key]))
        self.assertEqual( 200, response.status_code )
        self.assertEquals("application/json", response["content-type"])
        self.assertEqual( len(json.loads(response.content)), 2 )
        self.assertContains(response, "test@test", count=1)
        self.assertContains(response, "asd@asd", count=1)

        # vm without users who have admin perms
        response = c.get( reverse("instance-keys", args=[cluster.slug, vm1.hostname, key]))
        self.assertEqual( 200, response.status_code )
        self.assertEquals("application/json", response["content-type"])
        self.assertEqual( len(json.loads(response.content)), 0 )
        self.assertNotContains(response, "test@test")
        self.assertNotContains(response, "asd@asd")

    def test_view_modify(self):
        """
        Test modifying an instance
        """
        vm = globals()['vm']
        args = (cluster.slug, vm.hostname)
        url = '/cluster/%s/%s/edit' % args
    
        user = User(id=52, username='modifier')
        user.set_password('secret2')
        user.save()

        ## GET
        # Anonymous User
        response = c.get(url)
        self.assertEqual(302, response.status_code)

        # User with Modify Permissions
        user.grant('modify', vm)
        self.assertTrue(c.login(username=user.username, password='secret2'))
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.has_perm('modify', vm))
        self.assertFalse(user.has_perm('admin', vm))
        response = c.get(url)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/edit.html')
        user.revoke_all(vm)
        c.logout()

        # User with Admin Permissions
        user.grant('admin', vm)
        self.assertTrue(c.login(username=user.username, password='secret2'))
        self.assertFalse(user.is_superuser)
        response = c.get(url)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/edit.html')
        user.revoke_all(vm)
        c.logout()

        # Superuser
        user.is_superuser = True
        user.save()
        self.assertTrue(c.login(username=user.username, password='secret2'))
        response = c.get(url)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/edit.html')
        c.logout()
        user.is_superuser = False
        user.save()

        ## POST
        os_list = cluster_os_list(cluster)
        data = dict(vcpus=2,
            acpi=True,
            disk_cache='default',
            initrd_path='',
            kernel_args='ro',
            kvm_flag='',
            mem_path='',
            migration_downtime=30,
            security_domain='',
            security_model='none',
            usb_mouse='',
            use_chroot=False,
            use_localtime=False,
            vnc_bind_addres='0.0.0.0',
            vnc_tls=False,
            vnc_x509_path='',
            vnc_x509_verify=False,
            memory=512,
            os='image+debian-osgeo',
            disk_type='paravirtual',
            boot_order='disk',
            nic_type='paravirtual',
            nic_link='br0',
            nic_mac='aa:bb:00:00:33:d2',
            root_path='/dev/vda1',
            kernel_path='/boot/vmlinuz-2.32.6-27-generic',
            serial_console=True,
            cdrom_image_path='')
        
        # Required Values
        user.grant('modify', vm)
        self.assertTrue(c.login(username=user.username, password='secret2'))
        session = c.session
        session['os_list'] = os_list
        session.save()
        for property in ['vcpus', 'memory', 'disk_type', 'boot_order', 'nic_type', \
            'root_path']:
            data_ = data.copy()
            del data_[property]
            self.assertFalse(user.is_superuser)
            response = c.post(url, data_)
            # If failure then a field that is not required by the model, but
            #  should be required by the form, is not being required by
            #  the form. See the ModifyVirtualMachineForm.required field.
            self.assertNotEqual(response.context['form'][property].errors, [])
            self.assertEqual(200, response.status_code) # 302 if success (BAD)
            self.assertEqual('text/html; charset=utf-8', response['content-type'])
            self.assertTemplateUsed(response, 'virtual_machine/edit.html')
        c.logout()
        user.revoke_all(vm)
    

        # Anonymous User
        response = c.post(url, data)
        self.assertEqual(302, response.status_code)

        # Superuser
        user.is_superuser = True
        user.save()
        self.assertTrue(c.login(username=user.username, password='secret2'))
        self.assertTrue(user.is_superuser)
        session = c.session
        session['os_list'] = os_list
        session.save()
        response = c.post(url, data)
        self.assertEqual(302, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        c.logout()
        user.is_superuser = False
        user.save()

        # User without Permissions
        self.assertTrue(c.login(username=user.username, password='secret2'))
        self.assertFalse(user.is_superuser)
        session = c.session
        session['os_list'] = os_list
        session.save()
        response = c.post(url, data)
        self.assertEqual(403, response.status_code)
        self.assertTrue(response.context['message'])
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, '403.html')
        c.logout()
        
        # User with Modify Permissions
        user.grant('modify', vm)
        self.assertTrue(c.login(username=user.username, password='secret2'))
        self.assertFalse(user.is_superuser)
        session = c.session
        session['os_list'] = os_list
        session.save()
        response = c.post(url, data)
        self.assertEqual(302, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        user.revoke_all(vm)
        c.logout()

        # User with Admin Permissions
        user.grant('admin', vm)
        self.assertTrue(c.login(username=user.username, password='secret2'))
        self.assertFalse(user.is_superuser)
        session = c.session
        session['os_list'] = os_list
        session.save()
        response = c.post(url, data)
        self.assertEqual(302, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        user.revoke_all(vm)
        c.logout()        

    def test_view_modify_confirm(self):
        """
        Test confirm page for modifying an instance
        """
        vm = globals()['vm']
        args = (cluster.slug, vm.hostname)
        url = '/cluster/%s/%s/edit/confirm' % args
    
        user = User(id=52, username='modifier')
        user.set_password('secret2')
        user.save()
        
        os_list = cluster_os_list(cluster)
        edit_form = dict(vcpus=2,
            acpi=True,
            disk_cache='default',
            initrd_path='',
            kernel_args='ro',
            kvm_flag='',
            mem_path='',
            migration_downtime=30,
            security_domain='',
            security_model='none',
            usb_mouse='',
            use_chroot=False,
            use_localtime=False,
            vnc_bind_addres='0.0.0.0',
            vnc_tls=False,
            vnc_x509_path='',
            vnc_x509_verify=False,
            memory=512,
            os='image+debian-osgeo',
            disk_type='paravirtual',
            boot_order='disk',
            nic_type='paravirtual',
            nic_link='br0',
            nic_mac='aa:bb:00:00:33:d2',
            root_path='/dev/vda1',
            kernel_path='/boot/vmlinuz-2.32.6-27-generic',
            serial_console=True,
            cdrom_image_path='')

        ## SESSION VARIABLES
        # Make sure session variables are set
        user.is_superuser = True
        user.save()
        self.assertTrue(c.login(username=user.username, password='secret2'))
        session = c.session
        # edit_form 
        response = c.get(url)
        self.assertEqual(400, response.status_code)
        session['edit_form'] = edit_form
        session.save()
        response = c.get(url)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/edit_confirm.html')
        
        #session['os_list'] = os_list
        #session.save()
        user.revoke_all(vm)
        user.is_superuser = False
        user.save()
        c.logout()

        ## GET
        # Anonymous User
        response = c.get(url)
        self.assertEqual(302, response.status_code)

        # User with Modify Permissions
        user.grant('modify', vm)
        self.assertFalse(user.is_superuser)
        self.assertTrue(c.login(username=user.username, password='secret2'))
        session = c.session
        session['edit_form'] = edit_form
        session['os_list'] = os_list
        session.save()
        response = c.get(url)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/edit_confirm.html')
        user.revoke_all(vm)
        c.logout()

        # User with Admin Permissions
        user.grant('admin', vm)
        self.assertFalse(user.is_superuser)
        self.assertTrue(c.login(username=user.username, password='secret2'))
        session = c.session
        session['edit_form'] = edit_form
        session['os_list'] = os_list
        session.save()
        response = c.get(url)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/edit_confirm.html')
        user.revoke_all(vm)
        c.logout()

        # Superuser
        user.is_superuser = True
        user.save()
        self.assertTrue(c.login(username=user.username, password='secret2'))
        session = c.session
        session['edit_form'] = edit_form
        session['os_list'] = os_list
        session.save()
        response = c.get(url)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/edit_confirm.html')
        c.logout()
        user.is_superuser = False
        user.save()

        ## POST
        data = {'rapi_dict':json.dumps(edit_form)}
        # Anonymous User
        response = c.post(url, data)
        self.assertEqual(302, response.status_code)

        for i in ('cancel', 'edit', 'save', 'reboot'):
            data[i] = True

            # Superuser
            user.is_superuser = True
            user.save()
            self.assertTrue(c.login(username=user.username, password='secret2'))
            session = c.session
            session['edit_form'] = edit_form
            session['os_list'] = os_list
            session.save()
            self.assertTrue(user.is_superuser)
            response = c.post(url, data)
            self.assertEqual(302, response.status_code)
            self.assertEqual('text/html; charset=utf-8', response['content-type'])
            c.logout()
            user.is_superuser = False
            user.save()

            # User without Permissions
            self.assertTrue(c.login(username=user.username, password='secret2'))
            self.assertFalse(user.is_superuser)
            response = c.post(url, data)
            self.assertEqual(403, response.status_code)
            self.assertTrue(response.context['message'])
            self.assertEqual('text/html; charset=utf-8', response['content-type'])
            self.assertTemplateUsed(response, '403.html')
            c.logout()
            
            # User with Modify Permissions
            user.grant('modify', vm)
            self.assertTrue(c.login(username=user.username, password='secret2'))
            session = c.session
            session['edit_form'] = edit_form
            session['os_list'] = os_list
            session.save()
            self.assertFalse(user.is_superuser)
            response = c.post(url, data)
            self.assertEqual(302, response.status_code)
            self.assertEqual('text/html; charset=utf-8', response['content-type'])
            user.revoke_all(vm)
            c.logout()

            # User with Admin Permissions
            user.grant('admin', vm)
            self.assertTrue(c.login(username=user.username, password='secret2'))
            session = c.session
            session['edit_form'] = edit_form
            session['os_list'] = os_list
            session.save()
            self.assertFalse(user.is_superuser)
            response = c.post(url, data)
            self.assertEqual(302, response.status_code)
            self.assertEqual('text/html; charset=utf-8', response['content-type'])
            user.revoke_all(vm)
            c.logout()

            del data[i]

    def test_view_create_quota_first_vm(self):
        # XXX seperated from test_view_create_data since it was polluting the environment for later tests
        url = '/vm/add/%s'
        data = dict(cluster=cluster.id,
                    start=True,
                    owner=user.get_profile().id, #XXX remove this
                    hostname='new.vm.hostname',
                    disk_template='plain',
                    disk_size=1000,
                    memory=256,
                    vcpus=2,
                    root_path='/',
                    nic_type='paravirtual',
                    disk_type = 'paravirtual',
                    nic_link = 'br43',
                    nic_mode='routed',
                    boot_order='disk',
                    os='image+ubuntu-lucid',
                    pnode=cluster.nodes.all()[0],
                    snode=cluster.nodes.all()[1])


        # set up for testing quota on user's first VM
        user2 = User(id=43, username='quotatester')
        user2.set_password('secret')
        user2.grant('create_vm', cluster)
        user2.save()
        #print user2.__dict__
        #print user.__dict__
        profile = user2.get_profile()
        self.assert_(c.login(username=user2.username, password='secret'))

        # POST - over ram quota (user's first VM)
        self.assertEqual(profile.used_resources(cluster), {'ram': 0, 'disk': 0, 'virtual_cpus': 0})
        cluster.set_quota(profile, dict(ram=1000, disk=2000, virtual_cpus=10))
        data_ = data.copy()
        data_['memory'] = 2000
        data_['owner'] = profile.id
        response = c.post(url % '', data_)
        self.assertEqual(200, response.status_code) # 302 if vm creation succeeds
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/create.html')
        self.assertFalse(VirtualMachine.objects.filter(hostname='new.vm.hostname').exists())
        
        # POST - over disk quota (user's first WM)
        data_ = data.copy()
        data_['disk_size'] = 9001
        data_['owner'] = profile.id
        response = c.post(url % '', data_)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/create.html')
        self.assertFalse(VirtualMachine.objects.filter(hostname='new.vm.hostname').exists())
        
        # POST - over cpu quota (user's first VM)
        data_ = data.copy()
        data_['vcpus'] = 2000
        data_['owner'] = profile.id
        response = c.post(url % '', data_)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/create.html')
        self.assertFalse(VirtualMachine.objects.filter(hostname='new.vm.hostname').exists())

        # POST - over ram quota (user's first VM) (start = False)
        self.assertEqual(profile.used_resources(cluster), {'ram': 0, 'disk': 0, 'virtual_cpus': 0})
        cluster.set_quota(profile, dict(ram=1000, disk=2000, virtual_cpus=10))
        data_ = data.copy()
        data_['start'] = False
        data_['ram'] = 2000
        data_['owner'] = profile.id
        response = c.post(url % '', data_)
        self.assertEqual(302, response.status_code) # 302 if vm creation succeeds
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTrue(VirtualMachine.objects.filter(hostname='new.vm.hostname').exists())
        VirtualMachine.objects.filter(hostname='new.vm.hostname').delete()
        
        # POST - over disk quota (user's first VM) (start = False)
        data_ = data.copy()
        data_['start'] = False
        data_['disk_size'] = 9001
        data_['owner'] = profile.id
        response = c.post(url % '', data_)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/create.html')
        self.assertFalse(VirtualMachine.objects.filter(hostname='new.vm.hostname').exists())
        
        # POST - over cpu quota (user's first VM) (start = False)
        data_ = data.copy()
        data_['start'] = False
        data_['vcpus'] = 2000
        data_['owner'] = profile.id
        response = c.post(url % '', data_)
        self.assertEqual(302, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTrue(VirtualMachine.objects.filter(hostname='new.vm.hostname').exists())
        VirtualMachine.objects.filter(hostname='new.vm.hostname').delete()

        # clean up after quota tests
        self.assert_(c.login(username=user.username, password='secret'))

    def test_view_create_data(self):
        """
        Test creating a virtual machine
        with changes to the data
        """
        url = '/vm/add/%s'
        group1 = Group(id=81, name='testing_group2')
        group1.save()
        cluster1 = Cluster(hostname='test2.osuosl.bak', slug='OSL_TEST2')
        cluster1.save()
        data = dict(cluster=cluster.id,
                    start=True,
                    owner=user.get_profile().id, #XXX remove this
                    hostname='new.vm.hostname',
                    disk_template='plain',
                    disk_size=1000,
                    memory=256,
                    vcpus=2,
                    root_path='/',
                    nic_type='paravirtual',
                    disk_type = 'paravirtual',
                    nic_link = 'br43',
                    nic_mode='routed',
                    boot_order='disk',
                    os='image+ubuntu-lucid',
                    pnode=cluster.nodes.all()[0],
                    snode=cluster.nodes.all()[1])
        
        # login user
        self.assert_(c.login(username=user.username, password='secret'))
        
        # POST - invalid cluster
        user.grant('create_vm', cluster)
        data_ = data.copy()
        data_['cluster'] = -1
        response = c.post(url % '', data_)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/create.html')
        self.assertFalse(VirtualMachine.objects.filter(hostname='new.vm.hostname').exists())
        user.revoke_all(cluster)
        
        # POST - unauthorized for cluster selected (authorized for another)
        user.grant('create_vm', cluster1)
        user.is_superuser = False
        user.save()
        response = c.post(url % '', data_)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/create.html')
        self.assertFalse(VirtualMachine.objects.filter(hostname='new.vm.hostname').exists())
        
        # POST - required values
        for property in ['cluster', 'hostname', 'disk_size', 'disk_type','nic_type', 'nic_mode',
                         'vcpus', 'pnode', 'os', 'disk_template',
                         'root_path', 'boot_order']:
            data_ = data.copy()
            del data_[property]
            response = c.post(url % '', data_)
            self.assertEqual(200, response.status_code)
            self.assertEqual('text/html; charset=utf-8', response['content-type'])
            self.assertTemplateUsed(response, 'virtual_machine/create.html')
            self.assertFalse(VirtualMachine.objects.filter(hostname='new.vm.hostname').exists())
        
        # POST - over ram quota
        profile = user.get_profile()
        cluster.set_quota(profile, dict(ram=1000, disk=2000, virtual_cpus=10))
        vm = VirtualMachine(cluster=cluster, ram=100, disk_size=100, virtual_cpus=2, owner=profile).save()
        data_ = data.copy()
        data_['ram'] = 2000
        data_['owner'] = profile.id
        response = c.post(url % '', data_)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/create.html')
        self.assertFalse(VirtualMachine.objects.filter(hostname='new.vm.hostname').exists())
        
        # POST - over disk quota
        data_ = data.copy()
        data_['disk_size'] = 2000
        data_['owner'] = profile.id
        response = c.post(url % '', data_)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/create.html')
        self.assertFalse(VirtualMachine.objects.filter(hostname='new.vm.hostname').exists())
        
        # POST - over cpu quota
        data_ = data.copy()
        data_['vcpus'] = 2000
        data_['owner'] = profile.id
        response = c.post(url % '', data_)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/create.html')
        self.assertFalse(VirtualMachine.objects.filter(hostname='new.vm.hostname').exists())

        # POST invalid owner
        data_ = data.copy()
        data_['owner'] = -1
        response = c.post(url % '', data_)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/create.html')
        self.assertFalse(VirtualMachine.objects.filter(hostname='new.vm.hostname').exists())
        
        # POST - iallocator support
        user.grant('create_vm', cluster)
        data_ = data.copy()
        del data_['pnode']
        del data_['snode']
        data_['iallocator'] = True
        data_['iallocator_hostname'] = 'hail'
        self.assertFalse(VirtualMachine.objects.filter(hostname='new.vm.hostname').exists())
        response = c.post(url % '', data_, follow=True)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/create_status.html')
        new_vm = VirtualMachine.objects.get(hostname='new.vm.hostname')
        self.assertTrue(user.has_perm('admin', new_vm))
        VirtualMachine.objects.all().delete()
        user.revoke_all(cluster)
        user.revoke_all(new_vm)
        
        # POST - iallocator enabled, but none passed
        user.grant('create_vm', cluster)
        data_ = data.copy()
        data_['iallocator'] = True
        response = c.post(url % '', data_, follow=True)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/create.html')
        self.assertFalse(VirtualMachine.objects.filter(hostname='new.vm.hostname').exists())
        user.revoke_all(cluster)
        
        # POST - user authorized for cluster (create_vm)
        user.grant('create_vm', cluster)
        data_ = data.copy()
        self.assertFalse(VirtualMachine.objects.filter(hostname='new.vm.hostname').exists())
        response = c.post(url % '', data_, follow=True)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/create_status.html')
        new_vm = VirtualMachine.objects.get(hostname='new.vm.hostname')
        self.assertEqual(new_vm, response.context['instance'])
        self.assert_(user.has_perm('admin', new_vm))
        user.revoke_all(cluster)
        user.revoke_all(new_vm)
        VirtualMachine.objects.all().delete()
        
        # POST - user authorized for cluster (admin)
        user.grant('admin', cluster)
        response = c.post(url % '', data, follow=True)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/create_status.html')
        new_vm = VirtualMachine.objects.get(hostname='new.vm.hostname')
        self.assertEqual(new_vm, response.context['instance'])
        self.assert_(user.has_perm('admin', new_vm))
        VirtualMachine.objects.all().delete()
        user.revoke_all(cluster)
        user.revoke_all(new_vm)
        
        # POST - User attempting to be other user
        data_ = data.copy()
        data_['owner'] = user1.get_profile().id
        response = c.post(url % '', data_)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/create.html')
        self.assertFalse(VirtualMachine.objects.filter(hostname='new.vm.hostname').exists())
        
        # POST - user authorized for cluster (superuser)
        user.is_superuser = True
        user.save()
        response = c.post(url % '', data, follow=True)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/create_status.html')
        new_vm = VirtualMachine.objects.get(hostname='new.vm.hostname')
        self.assertEqual(new_vm, response.context['instance'])
        self.assert_(user.has_perm('admin', new_vm))
        user.revoke_all(new_vm)
        VirtualMachine.objects.all().delete()
        
        # POST - ganeti error
        cluster.rapi.CreateInstance.error = client.GanetiApiError('Testing Error')
        response = c.post(url % '', data)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/create.html')
        self.assertFalse(VirtualMachine.objects.filter(hostname='new.vm.hostname').exists())
        cluster.rapi.CreateInstance.error = None
        
        # POST - User attempting to be other user (superuser)
        data_ = data.copy()
        data_['owner'] = user1.get_profile().id
        response = c.post(url % '', data_, follow=True)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/create_status.html')
        new_vm = VirtualMachine.objects.get(hostname='new.vm.hostname')
        self.assertEqual(new_vm, response.context['instance'])
        self.assert_(user1.has_perm('admin', new_vm))
        self.assertEqual([], user.get_perms(new_vm))
        
        user.revoke_all(new_vm)
        user1.revoke_all(new_vm)
        VirtualMachine.objects.all().delete()
        
        # reset for group owner
        user.is_superuser = False
        user.save()
        data['owner'] = group.organization.id
        
        # POST - user is not member of group
        group.grant('create_vm', cluster)
        response = c.post(url % '', data)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/create.html')
        self.assertFalse(VirtualMachine.objects.filter(hostname='new.vm.hostname').exists())
        group.revoke_all(new_vm)
        VirtualMachine.objects.all().delete()
        
        # add user to group
        group.user_set.add(user)
        
        # POST - group authorized for cluster (create_vm)
        group.grant('create_vm', cluster)
        response = c.post(url % '', data, follow=True)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/create_status.html')
        new_vm = VirtualMachine.objects.get(hostname='new.vm.hostname')
        self.assertEqual(new_vm, response.context['instance'])
        self.assert_(group.has_perm('admin', new_vm))
        group.revoke_all(cluster)
        group.revoke_all(new_vm)
        VirtualMachine.objects.all().delete()
        
        # POST - group authorized for cluster (admin)
        group.grant('admin', cluster)
        response = c.post(url % '', data, follow=True)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/create_status.html')
        new_vm = VirtualMachine.objects.get(hostname='new.vm.hostname')
        self.assertEqual(new_vm, response.context['instance'])
        self.assert_(group.has_perm('admin', new_vm))
        group.revoke_all(cluster)
        group.revoke_all(new_vm)
        VirtualMachine.objects.all().delete()
        
        # POST - group authorized for cluster (superuser)
        user.is_superuser = True
        user.save()
        response = c.post(url % '', data, follow=True)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/create_status.html')
        new_vm = VirtualMachine.objects.get(hostname='new.vm.hostname')
        self.assertEqual(new_vm, response.context['instance'])
        self.assert_(group.has_perm('admin', new_vm))
        group.revoke_all(new_vm)
        VirtualMachine.objects.all().delete()
        
        # POST - not a group member (superuser)
        data_ = data.copy()
        data_['owner'] = group1.organization.id
        response = c.post(url % '', data_, follow=True)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/create_status.html')
        new_vm = VirtualMachine.objects.get(hostname='new.vm.hostname')
        self.assertEqual(new_vm, response.context['instance'])
        self.assert_(group1.has_perm('admin', new_vm))
        self.assertFalse(group.has_perm('admin', new_vm))
    
    def test_view_create(self):
        """
        Test viewing the create virtual machine page
        """
        url = '/vm/add/%s'
        group1 = Group(id=87, name='testing_group2')
        group1.save()
        cluster1 = Cluster(hostname='test2.osuosl.bak', slug='OSL_TEST2')
        cluster1.save()
        
        # anonymous user
        response = c.get(url % '', follow=True)
        self.assertEqual(200, response.status_code)
        self.assertTemplateUsed(response, 'registration/login.html')
        
        # unauthorized user
        self.assert_(c.login(username=user.username, password='secret'))
        response = c.post(url % '')
        self.assertEqual(403, response.status_code)
        
        # authorized GET (create_vm permissions)
        user.grant('create_vm', cluster)
        response = c.get(url % '')
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/create.html')
        user.revoke_all(cluster)
        
        # authorized GET (cluster admin permissions)
        user.grant('admin', cluster)
        response = c.get(url % '')
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/create.html')
        user.revoke_all(cluster)
        
        # authorized GET (superuser)
        user.is_superuser = True
        user.save()
        response = c.get(url % '')
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/create.html')
        
        # GET unknown cluster
        response = c.get(url % 'DOES_NOT_EXIST')
        self.assertEqual(404, response.status_code)
        
        # GET valid cluster
        response = c.get(url % cluster.slug)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/create.html')

    def test_view_create_recover(self):
        """
        Test the create view when recovering a failed vm

        Verifies:
            * vm can be successfully edited and created
            * vm object is reused
            * template object is reused
            * can only recover a vm in the failure state
            * owner cannot be changed (security)
            * editing user is not granted perms (security)
        """
        url = '/vm/add/'
        args = tuple()
        fail_template = 'virtual_machine/create.html'
        success_template = 'virtual_machine/create_status.html'

        template = VirtualMachineTemplate()
        template.save()

        # create a failed vm
        failed_vm, cluster2 = self.create_virtual_machine(cluster, 'failed.osuosl.org')
        failed_vm.owner=user.get_profile()
        failed_vm.template = template
        failed_vm.save()

        data = dict(cluster=cluster.id,
                    start=True,
                    owner=user.get_profile().id, #XXX remove this
                    hostname=failed_vm.hostname,
                    disk_template='plain',
                    disk_size=1000,
                    memory=256,
                    vcpus=2,
                    root_path='/',
                    nic_type='paravirtual',
                    disk_type = 'paravirtual',
                    nic_link = 'br43',
                    nic_mode='routed',
                    boot_order='disk',
                    os='image+ubuntu-lucid',
                    pnode=cluster.nodes.all()[0],
                    snode=cluster.nodes.all()[1])

        errors = [
                    {'hostname':vm.hostname}, # attempt to recover vm that hasn't failed
                    {'hostname':failed_vm.hostname, 'owner':user1.pk} # attempt to change owner
        ]
        self.assert_view_values(url, args, data, errors, fail_template)

        def tests(user, response):
            created_vm = VirtualMachine.objects.get(pk=failed_vm.pk)
            self.assertEqual(template.pk, created_vm.template_id)
            self.assertNotEqual(None, created_vm.last_job_id)
        users = [superuser]
        self.assert_200(url, args, users, success_template, data=data, method='post', tests=tests, follow=True)


    def test_view_load_recover(self):
        """
        Tests loading a VM that failed to deploy back into the create view
        for editing
        """
        url = '/cluster/%s/%s/recover/'
        args = (cluster.slug, vm.hostname)

        # vm with no template should redirect
        self.assert_200(url, args, [superuser], template='virtual_machine/detail.html', follow=True)

        template = VirtualMachineTemplate()
        template.save()
        vm.template = template
        vm.save()

        self.assert_standard_fails(url, args)
        users = [superuser, vm_admin, vm_modify, cluster_admin]
        self.assert_200(url, args, users, template='virtual_machine/create.html')


    def test_view_cluster_choices(self):
        """
        Test retrieving list of clusters a user or usergroup has access to
        """
        url = '/vm/add/choices/'
        Cluster.objects.all().delete()
        cluster0 = Cluster(hostname='user.create_vm', slug='user_create_vm')
        cluster0.save()
        cluster1 = Cluster(hostname='user.admin', slug='user_admin')
        cluster1.save()
        cluster2 = Cluster(hostname='superuser', slug='superuser')
        cluster2.save()
        cluster3 = Cluster(hostname='group.create_vm', slug='group_create_vm')
        cluster3.save()
        cluster4 = Cluster(hostname='group.admin', slug='group_admin')
        cluster4.save()
        cluster5 = Cluster(hostname='no.perms.on.this.group', slug='no_perms')
        cluster5.save()
        # cluster ids are 1 through 6
        
        group.user_set.add(user)
        group1 = Group(id=43, name='testing_group2')
        group1.save()
        group1.grant('admin',cluster5)
        
        # anonymous user
        response = c.get(url, follow=True)
        self.assertEqual(200, response.status_code)
        self.assertTemplateUsed(response, 'registration/login.html')
        
        self.assert_(c.login(username=user.username, password='secret'))

        # Invalid ClusterUser
        response = c.get(url, {'clusteruser_id':-1})
        self.assertEqual(404, response.status_code)

        # create_vm permission through a group
        group.grant('create_vm', cluster3)
        response = c.get(url, {'clusteruser_id': group.organization.id})
        self.assertEqual(200, response.status_code)
        clusters = json.loads(response.content)
        self.assert_([cluster3.id,'group.create_vm'] in clusters)
        self.assertEqual(1, len(clusters))

        # admin permission through a group
        group.grant('admin', cluster4)
        response = c.get(url, {'clusteruser_id': group.organization.id})
        self.assertEqual(200, response.status_code)
        clusters = json.loads(response.content)
        self.assert_([cluster3.id,'group.create_vm'] in clusters)
        self.assert_([cluster4.id,'group.admin'] in clusters)
        self.assertEqual(2, len(clusters))

        # create_vm permission on the user
        user.grant('create_vm', cluster0)
        response = c.get(url)
        self.assertEqual(200, response.status_code)
        clusters = json.loads(response.content)
        self.assert_([cluster0.id,'user.create_vm'] in clusters)
        self.assertEqual(1, len(clusters), clusters)

        # admin permission on the user
        user.grant('admin', cluster1)
        response = c.get(url)
        self.assertEqual(200, response.status_code)
        clusters = json.loads(response.content)
        self.assert_([cluster0.id,'user.create_vm'] in clusters)
        self.assert_([cluster1.id,'user.admin'] in clusters)
        self.assertEqual(2, len(clusters))

        # Superusers see everything
        user.is_superuser = True
        user.save()
        response = c.get(url)
        self.assertEqual(200, response.status_code)
        self.assertEqual('application/json', response['content-type'])
        clusters = json.loads(response.content)
        self.assert_([cluster0.id,'user.create_vm'] in clusters)
        self.assert_([cluster1.id,'user.admin'] in clusters)
        self.assert_([cluster2.id,'superuser'] in clusters, clusters)
        self.assert_([cluster3.id,'group.create_vm'] in clusters)
        self.assert_([cluster4.id,'group.admin'] in clusters, clusters)
        self.assert_([cluster5.id,'no.perms.on.this.group'] in clusters)
        self.assertEqual(6, len(clusters))
    
    def test_view_cluster_options(self):
        """
        Test retrieving list of options a cluster has for vms
        """
        url = '/vm/add/options/?cluster_id=%s'
        args = cluster.id
        
        # anonymous user
        response = c.post(url % args, follow=True)
        self.assertEqual(200, response.status_code)
        self.assertTemplateUsed(response, 'registration/login.html')
        
        # unauthorized user
        self.assert_(c.login(username=user.username, password='secret'))
        response = c.get(url % args)
        self.assertEqual(403, response.status_code)
        
        # invalid cluster
        response = c.get(url % "-4")
        self.assertEqual(404, response.status_code)
        
        # authorized (create_vm)
        grant(user, 'create_vm', cluster)
        response = c.get(url % args)
        self.assertEqual(200, response.status_code)
        self.assertEqual('application/json', response['content-type'])
        content = json.loads(response.content)
        self.assertEqual([u'gtest1.osuosl.bak', u'gtest2.osuosl.bak', u'gtest3.osuosl.bak'], content['nodes'])
        self.assertEqual(content["os"],
            [[u'Image',
                [[u'image+debian-osgeo', u'Debian Osgeo'],
                [u'image+ubuntu-lucid', u'Ubuntu Lucid']]
            ]]
        )
        user.revoke_all(cluster)
        
        # authorized (cluster admin)
        grant(user, 'admin', cluster)
        response = c.get(url % args)
        self.assertEqual(200, response.status_code)
        self.assertEqual('application/json', response['content-type'])
        content = json.loads(response.content)
        
        self.assertEqual([u'gtest1.osuosl.bak', u'gtest2.osuosl.bak', u'gtest3.osuosl.bak'], content['nodes'])
        self.assertEqual(content["os"],
            [[u'Image',
                [[u'image+debian-osgeo', u'Debian Osgeo'],
                [u'image+ubuntu-lucid', u'Ubuntu Lucid']]
            ]]
        )
        user.revoke_all(cluster)
        
        # authorized (superuser)
        user.is_superuser = True
        user.save()
        response = c.get(url % args)
        self.assertEqual(200, response.status_code)
        self.assertEqual('application/json', response['content-type'])
        content = json.loads(response.content)
        self.assertEqual([u'gtest1.osuosl.bak', u'gtest2.osuosl.bak', u'gtest3.osuosl.bak'], content['nodes'])
        self.assertEqual(content["os"],
            [[u'Image',
                [[u'image+debian-osgeo', u'Debian Osgeo'],
                [u'image+ubuntu-lucid', u'Ubuntu Lucid']]
            ]]
        )
    
    def test_view_cluster_defaults(self):
        """
        Test retrieval of dict of default parameters set on cluster
        """
        url = '/vm/add/defaults/?cluster_id=%s'
        args = cluster.id        
        
        expected = dict(
            boot_order='disk',
            memory=512,
            nic_type='paravirtual',
            root_path='/dev/vda2',
            hypervisors=['kvm'],
            serial_console=True,
            cdrom_image_path='',
            disk_type ='paravirtual',
            nic_link ='br42',
            nic_mode='bridged',
            vcpus=2,
            iallocator='',
            kernel_path=''
        )
        
        #anonymous users
        response = c.post(url % args, follow=True)
        self.assertEqual(200, response.status_code)
        self.assertTemplateUsed(response, 'registration/login.html')
        
        #unauthorized user
        self.assert_(c.login(username=user.username, password='secret'))
        response = c.get(url % args)
        self.assertEqual(403, response.status_code)
        
        #invalid cluster
        response = c.get(url % "-2")
        self.assertEqual(404, response.status_code)
        
        #authorized (create_vm)
        grant(user, 'create_vm', cluster)
        response = c.get(url % args)
        self.assertEqual(200, response.status_code)
        self.assertEqual('application/json', response['content-type'])
        content = json.loads(response.content)
        self.assertEqual(expected, content)
        user.revoke_all(cluster)
        
        #authorized (admin)
        grant(user, 'admin', cluster)
        response = c.get(url % args)
        self.assertEqual(200, response.status_code)
        self.assertEqual('application/json', response['content-type'])
        content = json.loads(response.content)
        self.assertEqual(expected, content)
        user.revoke_all(cluster)
        
        #authorized (superuser)
        user.is_superuser = True
        user.save()
        response = c.get(url % args)
        self.assertEqual(200, response.status_code)
        self.assertEqual('application/json', response['content-type'])
        content = json.loads(response.content)
        self.assertEqual(expected, content)
        user.is_superuser = False
        user.save()
    
    def test_view_delete(self):
        """
        Tests view for deleting virtual machines
        """
        url = '/cluster/%s/%s/delete'
        args = (cluster.slug, vm.hostname)
        
        # anonymous user
        response = c.get(url % args, follow=True)
        self.assertEqual(200, response.status_code)
        self.assertTemplateUsed(response, 'registration/login.html')
        
        # unauthorized user
        self.assert_(c.login(username=user.username, password='secret'))
        response = c.post(url % args)
        self.assertEqual(403, response.status_code)
        
        # invalid vm
        response = c.get(url % (cluster.slug, "DoesNotExist"))
        self.assertEqual(404, response.status_code)
        
        # authorized GET (vm remove permissions)
        user.grant('remove', vm)
        response = c.get(url % args)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/delete.html')
        self.assert_(VirtualMachine.objects.filter(id=vm.id).exists())
        user.revoke_all(vm)
        
        # authorized GET (vm admin permissions)
        user.grant('admin', vm)
        response = c.get(url % args)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/delete.html')
        self.assert_(VirtualMachine.objects.filter(id=vm.id).exists())
        user.revoke_all(cluster)
        
        # authorized GET (cluster admin permissions)
        user.grant('admin', cluster)
        response = c.get(url % args)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/delete.html')
        self.assert_(VirtualMachine.objects.filter(id=vm.id).exists())
        user.revoke_all(cluster)
        
        # authorized GET (superuser)
        user.is_superuser = True
        user.save()
        response = c.get(url % args)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/delete.html')
        self.assert_(VirtualMachine.objects.filter(id=vm.id).exists())
        
        #authorized POST (superuser)
        user1.grant('power', vm)
        vm.rapi.GetJobStatus.response = JOB_RUNNING
        response = c.post(url % args, follow=True)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/delete_status.html')
        self.assert_(VirtualMachine.objects.filter(id=vm.id).exists())
        pending_delete, job_id = VirtualMachine.objects.filter(id=vm.id).values('pending_delete','last_job_id')[0]
        self.assert_(pending_delete)
        self.assert_(job_id)
        user.is_superuser = False
        user.save()
        vm.save()
        
        #authorized POST (cluster admin)
        user.grant('admin', cluster)
        user1.grant('power', vm)
        response = c.post(url % args, follow=True)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/delete_status.html')
        self.assert_(VirtualMachine.objects.filter(id=vm.id).exists())
        pending_delete, job_id = VirtualMachine.objects.filter(id=vm.id).values('pending_delete','last_job_id')[0]
        self.assert_(pending_delete)
        self.assert_(job_id)
        user.revoke_all(cluster)
        
        #authorized POST (vm admin)
        vm.save()
        user.grant('admin', vm)
        user1.grant('power', vm)
        response = c.post(url % args, follow=True)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/delete_status.html')
        self.assert_(VirtualMachine.objects.filter(id=vm.id).exists())
        pending_delete, job_id = VirtualMachine.objects.filter(id=vm.id).values('pending_delete','last_job_id')[0]
        self.assert_(pending_delete)
        self.assert_(job_id)
        vm.save()
        user.revoke_all(vm)
        
        #authorized POST (cluster admin)
        vm.save()
        user.grant('remove', vm)
        user1.grant('power', vm)
        response = c.post(url % args, follow=True)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/delete_status.html')
        self.assert_(VirtualMachine.objects.filter(id=vm.id).exists())
        pending_delete, job_id = VirtualMachine.objects.filter(id=vm.id).values('pending_delete','last_job_id')[0]
        self.assert_(pending_delete)
        self.assert_(job_id)
        vm.save()
        user.revoke_all(vm)

    def test_view_reinstall(self):
        """
        Tests view for reinstalling virtual machines
        """
        url = '/cluster/%s/%s/reinstall'
        args = (cluster.slug, vm.hostname)
        
        # anonymous user
        response = c.get(url % args, follow=True)
        self.assertEqual(200, response.status_code)
        self.assertTemplateUsed(response, 'registration/login.html')
        
        # unauthorized user
        self.assert_(c.login(username=user.username, password='secret'))
        response = c.post(url % args)
        self.assertEqual(403, response.status_code)
        
        # invalid vm
        response = c.get(url % (cluster.slug, "DoesNotExist"))
        self.assertEqual(404, response.status_code)
        
        # authorized GET (vm remove permissions)
        user.grant('remove', vm)
        response = c.get(url % args)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/reinstall.html')
        self.assert_(VirtualMachine.objects.filter(id=vm.id).exists())
        user.revoke_all(vm)
        
        # authorized GET (vm admin permissions)
        user.grant('admin', vm)
        response = c.get(url % args)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/reinstall.html')
        self.assert_(VirtualMachine.objects.filter(id=vm.id).exists())
        user.revoke_all(cluster)
        
        # authorized GET (cluster admin permissions)
        user.grant('admin', cluster)
        response = c.get(url % args)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/reinstall.html')
        self.assert_(VirtualMachine.objects.filter(id=vm.id).exists())
        user.revoke_all(cluster)
        
        # authorized GET (superuser)
        user.is_superuser = True
        user.save()
        response = c.get(url % args)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'virtual_machine/reinstall.html')
        self.assert_(VirtualMachine.objects.filter(id=vm.id).exists())
        
        #authorized POST (superuser)
        response = c.post(url % args)
        self.assertEqual(302, response.status_code)
        user.is_superuser = False
        user.save()
        vm.save()
        
        #authorized POST (cluster admin)
        user.grant('admin', cluster)
        response = c.post(url % args)
        self.assertEqual(302, response.status_code)
        user.revoke_all(cluster)
        
        #authorized POST (vm admin)
        vm.save()
        user.grant('admin', vm)
        response = c.post(url % args)
        self.assertEqual(302, response.status_code)
        vm.save()
        user.revoke_all(vm)
        
        #authorized POST (cluster admin)
        vm.save()
        user.grant('remove', vm)
        response = c.post(url % args)
        self.assertEqual(302, response.status_code)
        vm.save()
        user.revoke_all(vm)
    
    def test_view_vnc(self):
        """
        Tests view for cluster Ajax vnc (noVNC) script:
        
        Verifies:
            * lack of permissions returns 403
            * nonexistent Cluster returns 404
            * nonexistent VirtualMachine returns 404
        """
        url = "/cluster/%s/%s/vnc/"
        args = (cluster.slug, vm.hostname)
        self.validate_get(url, args, 'virtual_machine/novnc.html')
    
    def test_view_vnc_proxy(self):
        """
        Tests view for cluster users:
        
        Verifies:
            * lack of permissions returns 403
            * nonexistent Cluster returns 404
            * nonexistent VirtualMachine returns 404
            * no ports set (not running proxy)
        """
        url = "/cluster/%s/%s/vnc_proxy/"
        args = (cluster.slug, vm.hostname)
        response = self.validate_get_configurable(url, args, None,
            "application/json", ["admin",])

    def test_view_object_log(self):
        """
        Tests view for object log:

        Verifies:
            * lack of permissions returns 403
            * nonexistent Cluster returns 404
            * nonexistent VirtualMachine returns 404
        """
        url = "/cluster/%s/%s/object_log/"
        args = (cluster.slug, vm.hostname)
        self.validate_get(url, args, 'object_log/log.html')

    def test_view_users(self):
        """
        Tests view for cluster users:
        
        Verifies:
            * lack of permissions returns 403
            * nonexistent Cluster returns 404
            * nonexistent VirtualMachine returns 404
        """
        url = "/cluster/%s/%s/users/"
        args = (cluster.slug, vm.hostname)
        self.validate_get(url, args, 'object_permissions/permissions/users.html')
    
    def test_view_add_permissions(self):
        """
        Test adding permissions to a new User or Group
        """
        url = '/cluster/%s/%s/permissions/'
        args = (cluster.slug, vm.hostname)
        
        # anonymous user
        response = c.get(url % args, follow=True)
        self.assertEqual(200, response.status_code)
        self.assertTemplateUsed(response, 'registration/login.html')
        
        # unauthorized user
        self.assert_(c.login(username=user.username, password='secret'))
        response = c.get(url % args)
        self.assertEqual(403, response.status_code)
        
        # nonexisent cluster
        response = c.get(url % ("DOES_NOT_EXIST", vm.hostname))
        self.assertEqual(404, response.status_code)
        
        # nonexisent vm
        response = c.get(url % (cluster.slug, "DOES_NOT_EXIST"))
        self.assertEqual(404, response.status_code)
        
        # valid GET authorized user (perm)
        grant(user, 'admin', vm)
        response = c.get(url % args)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'object_permissions/permissions/form.html')
        user.revoke('admin', vm)
        
        # valid GET authorized user (cluster admin)
        grant(user, 'admin', cluster)
        response = c.get(url % args)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'object_permissions/permissions/form.html')
        user.revoke('admin', cluster)
        
        # valid GET authorized user (superuser)
        user.is_superuser = True
        user.save()
        response = c.get(url % args)
        self.assertEqual(200, response.status_code)
        self.assertTemplateUsed(response, 'object_permissions/permissions/form.html')
        
        # no user or group
        data = {'permissions':['admin'], 'obj':vm.pk}
        response = c.post(url % args, data)
        self.assertEqual(200, response.status_code)
        self.assertEqual('application/json', response['content-type'])
        self.assertNotEqual('0', response.content)
        
        # both user and group
        data = {'permissions':['admin'], 'group':group.id, 'user':user1.id, 'obj':vm.pk}
        response = c.post(url % args, data)
        self.assertEqual(200, response.status_code)
        self.assertEqual('application/json', response['content-type'])
        self.assertNotEqual('0', response.content)
        
        # no permissions specified - user
        data = {'permissions':[], 'user':user1.id, 'obj':vm.pk}
        response = c.post(url % args, data)
        self.assertEqual(200, response.status_code)
        self.assertEqual('application/json', response['content-type'])
        self.assertNotEqual('0', response.content)
        
        # no permissions specified - group
        data = {'permissions':[], 'group':group.id, 'obj':vm.pk}
        response = c.post(url % args, data)
        self.assertEqual(200, response.status_code)
        self.assertEqual('application/json', response['content-type'])
        
        # valid POST user has permissions
        user1.grant('power', vm)
        data = {'permissions':['admin'], 'user':user1.id, 'obj':vm.pk}
        response = c.post(url % args, data)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'object_permissions/permissions/user_row.html')
        self.assert_(user1.has_perm('admin', vm))
        self.assertFalse(user1.has_perm('power', vm))
        
        # valid POST group has permissions
        group.grant('power', vm)
        data = {'permissions':['admin'], 'group':group.id, 'obj':vm.pk}
        response = c.post(url % args, data)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'object_permissions/permissions/group_row.html')
        self.assertEqual(['admin'], group.get_perms(vm))
    
    def test_view_user_permissions(self):
        """
        Tests updating User's permissions
        
        Verifies:
            * anonymous user returns 403
            * lack of permissions returns 403
            * nonexistent cluster returns 404
            * invalid user returns 404
            * invalid group returns 404
            * missing user and group returns error as json
            * GET returns html for form
            * If user/group has permissions no html is returned
            * If user/group has no permissions a json response of -1 is returned
        """
        args = (cluster.slug, vm.hostname, user1.id)
        args_post = (cluster.slug, vm.hostname)
        url = "/cluster/%s/%s/permissions/user/%s"
        url_post = "/cluster/%s/%s/permissions/"
        
        # anonymous user
        response = c.get(url % args, follow=True)
        self.assertEqual(200, response.status_code)
        self.assertTemplateUsed(response, 'registration/login.html')
        
        # unauthorized user
        self.assert_(c.login(username=user.username, password='secret'))
        response = c.get(url % args)
        self.assertEqual(403, response.status_code)
        
        # nonexisent cluster
        response = c.get(url % ("DOES_NOT_EXIST", vm.hostname, user1.id))
        self.assertEqual(404, response.status_code)
        
        # nonexisent vm
        response = c.get(url % (cluster.slug, "DOES_NOT_EXIST", user1.id))
        self.assertEqual(404, response.status_code)
        
        # valid GET authorized user (perm)
        grant(user, 'admin', vm)
        response = c.get(url % args)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'object_permissions/permissions/form.html')
        user.revoke('admin', vm)
        
        # valid GET authorized user (cluster admin)
        grant(user, 'admin', cluster)
        response = c.get(url % args)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'object_permissions/permissions/form.html')
        user.revoke('admin', cluster)
        
        # valid GET authorized user (superuser)
        user.is_superuser = True
        user.save()
        response = c.get(url % args)
        self.assertEqual(200, response.status_code)
        self.assertTemplateUsed(response, 'object_permissions/permissions/form.html')
        
        # invalid user
        response = c.get(url % (cluster.slug, vm.hostname, -1))
        self.assertEqual(404, response.status_code)
        
        # invalid user (POST)
        user1.grant('power', vm)
        data = {'permissions':['admin'], 'user':-1, 'obj':vm.pk}
        response = c.post(url_post % args_post, data)
        self.assertEqual('application/json', response['content-type'])
        self.assertNotEqual('0', response.content)
        
        # no user (POST)
        user1.grant('power', vm)
        data = {'permissions':['admin'], 'obj':vm.pk}
        response = c.post(url_post % args_post, data)
        self.assertEqual('application/json', response['content-type'])
        self.assertNotEqual('0', response.content)
        
        # valid POST user has permissions
        user1.grant('power', vm)
        data = {'permissions':['admin'], 'user':user1.id, 'obj':vm.pk}
        response = c.post(url_post % args_post, data)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'object_permissions/permissions/user_row.html')
        self.assert_(user1.has_perm('admin', vm))
        self.assertFalse(user1.has_perm('power', vm))
        
        # valid POST user has no permissions left
        data = {'permissions':[], 'user':user1.id, 'obj':vm.pk}
        response = c.post(url_post % args_post, data)
        self.assertEqual(200, response.status_code)
        self.assertEqual('application/json', response['content-type'])
        self.assertEqual([], get_user_perms(user, vm))
        self.assertEqual('"user_88"', response.content)
    
    def test_view_group_permissions(self):
        """
        Test editing Group permissions on a Cluster
        """
        args = (cluster.slug, vm.hostname, group.id)
        args_post = (cluster.slug, vm.hostname)
        url = "/cluster/%s/%s/permissions/group/%s"
        url_post = "/cluster/%s/%s/permissions/"
        
        # anonymous user
        response = c.get(url % args, follow=True)
        self.assertEqual(200, response.status_code)
        self.assertTemplateUsed(response, 'registration/login.html')
        
        # unauthorized user
        self.assert_(c.login(username=user.username, password='secret'))
        response = c.get(url % args)
        self.assertEqual(403, response.status_code)
        
        # nonexisent cluster
        response = c.get(url % ("DOES_NOT_EXIST", vm.hostname, group.id))
        self.assertEqual(404, response.status_code)
        
        # nonexisent vm
        response = c.get(url % (cluster.slug, "DOES_NOT_EXIST", user1.id))
        self.assertEqual(404, response.status_code)
        
        # valid GET authorized user (perm)
        grant(user, 'admin', vm)
        response = c.get(url % args)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'object_permissions/permissions/form.html')
        user.revoke('admin', vm)
        
        # valid GET authorized user (cluster admin)
        grant(user, 'admin', cluster)
        response = c.get(url % args)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'object_permissions/permissions/form.html')
        user.revoke('admin', cluster)
        
        # valid GET authorized user (superuser)
        user.is_superuser = True
        user.save()
        response = c.get(url % args)
        self.assertEqual(200, response.status_code)
        self.assertTemplateUsed(response, 'object_permissions/permissions/form.html')
        
        # invalid group
        response = c.get(url % (cluster.slug, vm.hostname, 0))
        self.assertEqual(404, response.status_code)
        
        # invalid group (POST)
        data = {'permissions':['admin'], 'group':-1, 'obj':vm.pk}
        response = c.post(url_post % args_post, data)
        self.assertEqual('application/json', response['content-type'])
        self.assertNotEqual('0', response.content)
        
        # no group (POST)
        data = {'permissions':['admin'], 'obj':vm.pk}
        response = c.post(url_post % args_post, data)
        self.assertEqual('application/json', response['content-type'])
        self.assertNotEqual('0', response.content)
        
        # valid POST group has permissions
        group.grant('power', vm)
        data = {'permissions':['admin'], 'group':group.id, 'obj':vm.pk}
        response = c.post(url_post % args_post, data)
        self.assertEqual('text/html; charset=utf-8', response['content-type'])
        self.assertTemplateUsed(response, 'object_permissions/permissions/group_row.html')
        self.assertEqual(['admin'], group.get_perms(vm))
        
        # valid POST group has no permissions left
        data = {'permissions':[], 'group':group.id, 'obj':vm.pk}
        response = c.post(url_post % args_post, data)
        self.assertEqual(200, response.status_code)
        self.assertEqual('application/json', response['content-type'])
        self.assertEqual([], group.get_perms(vm))
        self.assertEqual('"group_42"', response.content)

    def test_view_rename(self):
        """ tests renaming a VirtualMachine """
        url = "/cluster/%s/%s/rename/"
        args = (cluster.slug, vm.hostname)
        template = 'virtual_machine/rename.html'
        template_success = 'virtual_machine/detail.html'
        users =[superuser, cluster_admin, vm_admin, vm_modify]
        denied = [cluster_migrate]

        # test GET requests
        self.assert_standard_fails(url, args)
        self.assert_200(url, args, users, template=template)
        self.assert_403(url, args, denied)

        # test POST
        def tests(user, response):
            updated_vm = VirtualMachine.objects.get(pk=vm.pk)
            self.assertEqual('foo.arg.different', updated_vm.hostname)
            vm.save()
            
        data = {'hostname':'foo.arg.different', 'ip_check':False, 'name_check':False}
        self.assert_standard_fails(url, args, data, method='post')
        self.assert_200(url, args, users, template_success, data=data, follow=True, method="post", tests=tests)
        self.assert_403(url, args, denied, data=data, method="post")

        # test form errors
        def tests(user, response):
            updated_vm = VirtualMachine.objects.get(pk=vm.pk)
            self.assertEqual(vm.hostname, updated_vm.hostname)
        errors = ({'hostname':vm.hostname},)

        self.assert_view_missing_fields(url, args, data, fields=['hostname'], template=template, tests=tests)
        self.assert_view_values(url, args, data, errors, template, tests=tests)


class TestNewVirtualMachineForm(TestCase, VirtualMachineTestCaseMixin):
    
    def setUp(self):
        self.tearDown()
        models.client.GanetiRapiClient = RapiProxy
        cluster0 = Cluster(hostname='test0', slug='test0')
        cluster1 = Cluster(hostname='test1', slug='test1')
        cluster2 = Cluster(hostname='test2', slug='test2')
        cluster3 = Cluster(hostname='test3', slug='test3')
        cluster0.save()
        cluster1.save()
        cluster2.save()
        cluster3.save()
        cluster0.sync_nodes()
        cluster0.info = INFO
        
        user = User(id=67, username='tester0')
        user.set_password('secret')
        user.save()
        user1 = User(id=70, username='tester1')
        user1.set_password('secret')
        user1.save()
        group = Group(id=45, name='testing_group')
        group.save()
        
        g = globals()
        g['cluster0'] = cluster0
        g['cluster1'] = cluster1
        g['cluster2'] = cluster2
        g['cluster3'] = cluster3
        g['user'] = user
        g['user1'] = user1
        g['group'] = group

    def tearDown(self):
        User.objects.all().delete()
        Group.objects.all().delete()
        VirtualMachine.objects.all().delete()
        Cluster.objects.all().delete()
    
    def test_default_choices(self):
        """
        Test that ChoiceFields have the correct default options
        """
        form = NewVirtualMachineForm(user, None)
        self.assertEqual([
            (u'', u'---------'),
            (u'rtl8139',u'rtl8139'),
            (u'ne2k_isa',u'ne2k_isa'),
            (u'ne2k_pci',u'ne2k_pci'),
            (u'i82551',u'i82551'),
            (u'i82557b',u'i82557b'),
            (u'i82559er',u'i82559er'),
            (u'pcnet',u'pcnet'),
            (u'e1000',u'e1000'),
            (u'paravirtual',u'paravirtual'),
            ], form.fields['nic_type'].choices)
        self.assertEqual([
            (u'', u'---------'),
            (u'routed', u'routed'),
            (u'bridged', u'bridged')
            ], form.fields['nic_mode'].choices)
        self.assertEqual([('disk', 'Hard Disk'),
            ('cdrom', 'CD-ROM'),
            ('network', 'Network')
            ], form.fields['boot_order'].choices)
        self.assertEqual([
            (u'', u'---------'),
            (u'plain', u'plain'),
            (u'drbd', u'drbd'),
            (u'file', u'file'),
            (u'diskless', u'diskless')
            ], form.fields['disk_template'].choices)
    
    def test_cluster_init(self):
        """
        Tests initializing a form with a Cluster
        
        Verifies:
            * cluster choices are set correctly
            * node choices are set correctly
        """
        
        # no cluster
        form = NewVirtualMachineForm(user, None)
        self.assertEqual([(u'', u'---------')], form.fields['pnode'].choices)
        self.assertEqual([(u'', u'---------')], form.fields['snode'].choices)
        self.assertEqual([(u'', u'---------')], form.fields['os'].choices)
        
        # cluster provided
        form = NewVirtualMachineForm(user, cluster0)
        self.assertEqual([(u'', u'---------'), (u'gtest1.osuosl.bak', u'gtest1.osuosl.bak'), (u'gtest2.osuosl.bak', u'gtest2.osuosl.bak'), (u'gtest3.osuosl.bak', u'gtest3.osuosl.bak')], form.fields['pnode'].choices)
        self.assertEqual([(u'', u'---------'), (u'gtest1.osuosl.bak', u'gtest1.osuosl.bak'), (u'gtest2.osuosl.bak', u'gtest2.osuosl.bak'), (u'gtest3.osuosl.bak', u'gtest3.osuosl.bak')], form.fields['snode'].choices)
        self.assertEqual(form.fields['os'].choices,
            [
                (u'', u'---------'),
                ('Image',
                    [('image+debian-osgeo', 'Debian Osgeo'),
                    ('image+ubuntu-lucid', 'Ubuntu Lucid')]
                )
            ]
        )
        
        # cluster from initial data
        form = NewVirtualMachineForm(user, None, {'cluster':cluster0.id})
        self.assertEqual([(u'', u'---------'), (u'gtest1.osuosl.bak', u'gtest1.osuosl.bak'), (u'gtest2.osuosl.bak', u'gtest2.osuosl.bak'), (u'gtest3.osuosl.bak', u'gtest3.osuosl.bak')], form.fields['pnode'].choices)
        self.assertEqual([(u'', u'---------'), (u'gtest1.osuosl.bak', u'gtest1.osuosl.bak'), (u'gtest2.osuosl.bak', u'gtest2.osuosl.bak'), (u'gtest3.osuosl.bak', u'gtest3.osuosl.bak')], form.fields['snode'].choices)
        self.assertEqual(form.fields['os'].choices,
            [
                (u'', u'---------'),
                ('Image',
                    [('image+debian-osgeo', 'Debian Osgeo'),
                    ('image+ubuntu-lucid', 'Ubuntu Lucid')]
                )
            ]
        )
        
        # cluster from initial data
        form = NewVirtualMachineForm(user, cluster0, {'cluster':cluster0.id})
        self.assertEqual([(u'', u'---------'), (u'gtest1.osuosl.bak', u'gtest1.osuosl.bak'), (u'gtest2.osuosl.bak', u'gtest2.osuosl.bak'), (u'gtest3.osuosl.bak', u'gtest3.osuosl.bak')], form.fields['pnode'].choices)
        self.assertEqual([(u'', u'---------'), (u'gtest1.osuosl.bak', u'gtest1.osuosl.bak'), (u'gtest2.osuosl.bak', u'gtest2.osuosl.bak'), (u'gtest3.osuosl.bak', u'gtest3.osuosl.bak')], form.fields['snode'].choices)
        self.assertEqual(form.fields['os'].choices,
            [
                (u'', u'---------'),
                ('Image',
                    [('image+debian-osgeo', 'Debian Osgeo'),
                    ('image+ubuntu-lucid', 'Ubuntu Lucid')]
                )
            ]
        )
    
    def test_cluster_choices_init(self):
        """
        Tests that cluster choices are based on User permissions
        
        Verifies:
            * superusers have all Clusters as choices
            * if owner is set, only display clusters the owner has permissions
              directly on.  This includes both users and groups
            * if no owner is set, choices include clusters that the user has
              permission directly on, or through a group
        """
        
        # no owner, no permissions
        form = NewVirtualMachineForm(user, None)
        self.assertEqual(set([(u'', u'---------')]), set(form.fields['cluster'].choices))
        
        # no owner, group and direct permissions
        user.grant('admin', cluster0)
        user.grant('create_vm', cluster1)
        group.grant('admin', cluster2)
        group.user_set.add(user)
        self.assertEqual(set([(u'', u'---------'), (1, u'test0'), (2, u'test1'), (3, u'test2')]), set(form.fields['cluster'].choices))
        user.revoke_all(cluster0)
        user.revoke_all(cluster1)
        group.revoke_all(cluster2)
        
        # owner, user with no choices
        form = NewVirtualMachineForm(user, None, initial={'owner':user.get_profile().id})
        self.assertEqual(set([(u'', u'---------')]), set(form.fields['cluster'].choices))
        
        # owner, user with choices
        user.grant('admin', cluster0)
        user.grant('create_vm', cluster1)
        form = NewVirtualMachineForm(user, None, initial={'owner':user.get_profile().id})
        self.assertEqual(set([(u'', u'---------'), (1, u'test0'), (2, u'test1')]), set(form.fields['cluster'].choices))
        
        # owner, group with no choices
        form = NewVirtualMachineForm(user, None, initial={'owner':group.organization.id})
        self.assertEqual(set([(u'', u'---------')]), set(form.fields['cluster'].choices))
        
        # owner, group with choices
        group.grant('admin', cluster2)
        group.grant('create_vm', cluster3)
        form = NewVirtualMachineForm(user, None, initial={'owner':group.organization.id})
        self.assertEqual(set([(u'', u'---------'), (3, u'test2'), (4, u'test3')]), set(form.fields['cluster'].choices))
        
        # user - superuser
        user.is_superuser = True
        user.save()
        form = NewVirtualMachineForm(user, None, initial={'owner':user.get_profile().id})
        self.assertEqual(set([(u'', u'---------'), (1, u'test0'), (2, u'test1'), (3, u'test2'), (4, u'test3')]), set(form.fields['cluster'].choices))
        
        # group - superuser
        form = NewVirtualMachineForm(user, None, initial={'owner':group.organization.id})
        self.assertEqual(set([(u'', u'---------'), (1, u'test0'), (2, u'test1'), (3, u'test2'), (4, u'test3')]), set(form.fields['cluster'].choices))
    
    def test_owner_choices_init(self):
        """
        Tests that owner choices are set based on User permissions
        
        Verifies:
            * superusers have all clusterusers as choices
            * user receives themselves as a choice if they have perms
            * user receives all groups they are a member of
        """
        
        # user with no choices
        form = NewVirtualMachineForm(user, cluster0)
        self.assertEqual([(u'', u'---------')], form.fields['owner'].choices)
        
        # user with perms on self, no groups
        user.grant('admin', cluster0)
        form = NewVirtualMachineForm(user, None)
        self.assertEqual(
            [
                (u'', u'---------'),
                (user.profile.id, u'tester0'),
            ], form.fields['owner'].choices)
        user.set_perms(['create_vm'], cluster0)
        form = NewVirtualMachineForm(user, None)
        self.assertEqual(
            [
                (u'', u'---------'),
                (user.profile.id, u'tester0'),
            ], form.fields['owner'].choices)
        
        # user with perms on self and groups
        group.user_set.add(user)
        group.grant('admin', cluster0)
        form = NewVirtualMachineForm(user, None)
        self.assertEqual(
            [
                (u'', u'---------'),
                (group.organization.id, u'testing_group'),
                (user.profile.id, u'tester0'),
            ], form.fields['owner'].choices)
        user.revoke_all(cluster0)
        
        # user with no perms on self, but groups
        form = NewVirtualMachineForm(user, None)
        self.assertEqual(
            [
                (u'', u'---------'),
                (group.organization.id, u'testing_group'),
            ], form.fields['owner'].choices)
        group.set_perms(['create_vm'], cluster0)
        form = NewVirtualMachineForm(user, None)
        self.assertEqual(
            [
                (u'', u'---------'),
                (group.organization.id, u'testing_group'),
            ], form.fields['owner'].choices)
        group.revoke_all(cluster0)
        
        # superuser
        user.is_superuser = True
        user.save()
        form = NewVirtualMachineForm(user, None)
        self.assertEqual(
            [
                (u'', u'---------'),
                (user.profile.id, u'tester0'),
                (user1.profile.id, u'tester1'),
                (group.organization.id, u'testing_group'),
            ], list(form.fields['owner'].choices))


class TestVirtualMachineHelpers(TestCase):

    def test_os_prettify(self):
        """
        Test the os_prettify() helper function.
        """

        # Test a single entry.
        self.assertEqual(os_prettify(["hurp+durp"]),
            [
                ("Hurp",
                    [("hurp+durp", "Durp")]
                )
            ])

        # Test the example in the os_prettify() docstring.
        self.assertEqual(
            os_prettify([
                "image+obonto-hungry-hydralisk",
                "image+fodoro-core",
                "dobootstrop+dobion-lotso",
            ]), [
                ("Dobootstrop", [
                    ("dobootstrop+dobion-lotso", "Dobion Lotso"),
                ]),
                ("Image", [
                    ("image+obonto-hungry-hydralisk",
                        "Obonto Hungry Hydralisk"),
                    ("image+fodoro-core", "Fodoro Core"),
                ]),
            ])

        # Test entries that do not follow the pattern.
        # This one is from #2157. Still parses, just in a weird way.
        self.assertEqual(os_prettify(["debian-pressed+ia32"]),
            [('Debian-pressed', [('debian-pressed+ia32', 'Ia32')])])

        # Test that #2157 causes "Unknown" entries.
        self.assertEqual(os_prettify(["deb-ver1", "noop"]),
            [
                ("Unknown", [
                    ("deb-ver1", "deb-ver1"),
                    ("noop", "noop"),
                ]),
            ])
