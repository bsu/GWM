# Copyright (C) 2010 Oregon State University et al.
# Copyright (C) 2010 Greek Research and Technology Network
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


import json

from django import forms
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.urlresolvers import reverse
from django.db.models.query_utils import Q
from django.http import HttpResponse, HttpResponseRedirect, HttpResponseForbidden
from django.shortcuts import get_object_or_404, render_to_response
from django.template import RequestContext
from django.template.defaultfilters import slugify

from object_permissions import get_users_any
from object_permissions.views.permissions import view_users, view_permissions
from object_permissions import signals as op_signals

from object_log.models import LogItem
from object_log.views import list_for_object
from util.client import GanetiApiError

log_action = LogItem.objects.log_action

from ganeti.models import Cluster, ClusterUser, Profile, SSHKey
from ganeti.views import render_403, render_404
from ganeti.views.virtual_machine import render_vms
from ganeti.fields import DataVolumeField

# Regex for a resolvable hostname
FQDN_RE = r'^[\w]+(\.[\w]+)*$'


@login_required
def detail(request, cluster_slug):
    """
    Display details of a cluster
    """
    cluster = get_object_or_404(Cluster, slug=cluster_slug)
    user = request.user
    admin = True if user.is_superuser else user.has_perm('admin', cluster)
    if not admin:
        return render_403(request, "You do not have sufficient privileges")
    
    return render_to_response("cluster/detail.html", {
        'cluster':cluster,
        'admin':admin
        },
        context_instance=RequestContext(request),
    )


@login_required
def nodes(request, cluster_slug):
    """
    Display all nodes in a cluster
    """
    cluster = get_object_or_404(Cluster, slug=cluster_slug)
    user = request.user
    if not (user.is_superuser or user.has_perm('admin', cluster)):
        return render_403(request, "You do not have sufficient privileges")
    
    return render_to_response("node/table.html", \
                        {'cluster': cluster, 'nodes':cluster.nodes.all()}, \
        context_instance=RequestContext(request),
    )


@login_required
def virtual_machines(request, cluster_slug):
    """
    Display all virtual machines in a cluster.  Filtered by access the user
    has permissions for
    """
    cluster = get_object_or_404(Cluster, slug=cluster_slug)
    user = request.user
    admin = True if user.is_superuser else user.has_perm('admin', cluster)
    if not admin:
        return render_403(request, "You do not have sufficient privileges")

    vms = cluster.virtual_machines.select_related('cluster').all()
    vms = render_vms(request, vms)

    return render_to_response("virtual_machine/table.html", \
                {'cluster': cluster, 'vms':vms}, \
                context_instance=RequestContext(request))


@login_required
def edit(request, cluster_slug=None):
    """
    Edit a cluster
    """
    if cluster_slug:
        cluster = get_object_or_404(Cluster, slug=cluster_slug)
    else:
        cluster = None
    
    user = request.user
    if not (user.is_superuser or (cluster and user.has_perm('admin', cluster))):
        return render_403(request, "You do not have sufficient privileges")
    
    if request.method == 'POST':
        form = EditClusterForm(request.POST, instance=cluster)
        if form.is_valid():
            cluster = form.save()
            # TODO Create post signal to import
            #   virtual machines on edit of cluster
            if cluster.info is None:
                try:
                    cluster.sync_nodes()
                    cluster.sync_virtual_machines()
                except GanetiApiError:
                    # ganeti errors here are silently discarded.  It's
                    # valid to enter bad info.  A user might be adding
                    # info for an offline cluster.
                    pass

            log_action('EDIT', user, cluster)

            return HttpResponseRedirect(reverse('cluster-detail', \
                                                args=[cluster.slug]))
    
    elif request.method == 'DELETE':
        cluster.delete()
        return HttpResponse('1', mimetype='application/json')
    
    else:
        form = EditClusterForm(instance=cluster)
    
    return render_to_response("cluster/edit.html", {
        'form' : form,
        'cluster': cluster,
        },
        context_instance=RequestContext(request),
    )


@login_required
def list_(request):
    """
    List all clusters
    """
    user = request.user
    if user.is_superuser:
        cluster_list = Cluster.objects.all()
    else:
        cluster_list = user.get_objects_all_perms(Cluster, ['admin',])
    return render_to_response("cluster/list.html", {
        'cluster_list': cluster_list,
        'user': request.user,
        },
        context_instance=RequestContext(request),
    )


@login_required
def users(request, cluster_slug):
    """
    Display all of the Users of a Cluster
    """
    cluster = get_object_or_404(Cluster, slug=cluster_slug)
    
    user = request.user
    if not (user.is_superuser or user.has_perm('admin', cluster)):
        return render_403(request, "You do not have sufficient privileges")
    
    url = reverse('cluster-permissions', args=[cluster.slug])
    return view_users(request, cluster, url, template='cluster/users.html')


@login_required
def permissions(request, cluster_slug, user_id=None, group_id=None):
    """
    Update a users permissions. This wraps object_permissions.view_permissions()
    with our custom permissions checks.
    """
    cluster = get_object_or_404(Cluster, slug=cluster_slug)
    user = request.user
    if not (user.is_superuser or user.has_perm('admin', cluster)):
        return render_403(request, "You do not have sufficient privileges")

    url = reverse('cluster-permissions', args=[cluster.slug])
    return view_permissions(request, cluster, url, user_id, group_id,
                            user_template='cluster/user_row.html',
                            group_template='cluster/group_row.html')


def ssh_keys(request, cluster_slug, api_key):
    """
    Show all ssh keys which belong to users, who have any perms on the cluster
    """
    if settings.WEB_MGR_API_KEY != api_key:
        return HttpResponseForbidden("You're not allowed to view keys.")
    
    cluster = get_object_or_404(Cluster, slug=cluster_slug)

    users = set(get_users_any(cluster).values_list("id", flat=True))
    for vm in cluster.virtual_machines.all():
        users = users.union(set(get_users_any(vm).values_list('id', flat=True)))

    keys = SSHKey.objects \
        .filter(Q(user__in=users) | Q(user__is_superuser=True)) \
        .values_list('key','user__username') \
        .order_by('user__username')

    keys_list = list(keys)
    return HttpResponse(json.dumps(keys_list), mimetype="application/json")


class QuotaForm(forms.Form):
    """
    Form for editing user quota on a cluster
    """
    input = forms.TextInput(attrs={'size':5})
    
    user = forms.ModelChoiceField(queryset=ClusterUser.objects.all(), \
                                  widget=forms.HiddenInput)
    ram = DataVolumeField(label='Memory', required=False, min_value=0)
    virtual_cpus = forms.IntegerField(label='Virtual CPUs', required=False, \
                                    min_value=0, widget=input)
    disk = DataVolumeField(label='Disk Space', required=False, min_value=0)
    delete = forms.BooleanField(required=False, widget=forms.HiddenInput)

@login_required
def quota(request, cluster_slug, user_id):
    """
    Updates quota for a user
    """
    cluster = get_object_or_404(Cluster, slug=cluster_slug)
    user = request.user
    if not (user.is_superuser or user.has_perm('admin', cluster)):
        return render_403(request, "You do not have sufficient privileges")
    
    if request.method == 'POST':
        form = QuotaForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            cluster_user = data['user']
            if data['delete']:
                cluster.set_quota(cluster_user)
            else:
                quota = cluster.get_quota()
                same = data['virtual_cpus'] == quota['virtual_cpus'] \
                    and data['disk']==quota['disk'] \
                    and data['ram']==quota['ram']
                if same:
                    # same as default, set quota to default.
                    cluster.set_quota(cluster_user)
                else:
                    cluster.set_quota(cluster_user, data)
            
            # return updated html
            cluster_user = cluster_user.cast()
            url = reverse('cluster-permissions', args=[cluster.slug])
            if isinstance(cluster_user, (Profile,)):
                return render_to_response("cluster/user_row.html",
                    {'object':cluster, 'user':cluster_user.user, 'url':url})
            else:
                return render_to_response("cluster/group_row.html",
                    {'object':cluster, 'group':cluster_user.group, \
                     'url':url})
        
        # error in form return ajax response
        content = json.dumps(form.errors)
        return HttpResponse(content, mimetype='application/json')
    
    if user_id:
        cluster_user = get_object_or_404(ClusterUser, id=user_id)
        quota = cluster.get_quota(cluster_user)
        data = {'user':user_id}
        if quota:
            data.update(quota)
    else:
        return render_404(request, 'User was not found')
    
    form = QuotaForm(data)
    return render_to_response("cluster/quota.html", \
                        {'form':form, 'cluster':cluster, 'user_id':user_id}, \
                        context_instance=RequestContext(request))


class EditClusterForm(forms.ModelForm):
    class Meta:
        model = Cluster
        widgets = {
            'password' : forms.PasswordInput(),
        }
        
    ram = DataVolumeField(label='Memory', required=False, min_value=0)
    disk = DataVolumeField(label='Disk Space', required=False, min_value=0)
    
    def clean(self):
        self.cleaned_data = super(EditClusterForm, self).clean()
        data = self.cleaned_data
        host = data.get('hostname', None)
        user = data.get('username', None)
        new = data.get('password', None)
        
        # Automatically set the slug on cluster creation
        if not host:
            msg = 'Enter a hostname'
            self._errors['hostname'] = self.error_class([msg])
            
        if user: 
            if not new:
                if 'password' in data: del data['password']
                msg = 'Enter a password'
                self._errors['password'] = self.error_class([msg])
            
        elif new:
            msg = 'Enter a username'
            self._errors['username'] = self.error_class([msg])
            
            if not new:
                if 'password' in data: del data['password']
                msg = 'Enter a password'
                self._errors['password'] = self.error_class([msg])

        if 'hostname' in data and data['hostname'] and 'slug' not in data:
            data['slug'] = slugify(data['hostname'].split('.')[0])
            del self._errors['slug']
        
        return data


@login_required
def object_log(request, cluster_slug):
    """ displays object log for this cluster """
    cluster = get_object_or_404(Cluster, slug=cluster_slug)
    user = request.user
    if not (user.is_superuser or user.has_perm('admin', cluster)):
        return render_403(request, "You do not have sufficient privileges")
    return list_for_object(request, cluster)


def recv_user_add(sender, editor, user, obj, **kwargs):
    """
    receiver for object_permissions.signals.view_add_user, Logs action
    """
    log_action('ADD_USER', editor, obj, user)


def recv_user_remove(sender, editor, user, obj, **kwargs):
    """
    receiver for object_permissions.signals.view_remove_user, Logs action
    """
    log_action('REMOVE_USER', editor, obj, user)
    
    # remove custom quota user may have had.
    if isinstance(user, (User,)):
        cluster_user = user.get_profile()
    else:
        cluster_user = user.organization
    cluster_user.quotas.filter(cluster=obj).delete()


def recv_perm_edit(sender, editor, user, obj, **kwargs):
    """
    receiver for object_permissions.signals.view_edit_user, Logs action
    """
    log_action('MODIFY_PERMS', editor, obj, user)


op_signals.view_add_user.connect(recv_user_add, sender=Cluster)
op_signals.view_remove_user.connect(recv_user_remove, sender=Cluster)
op_signals.view_edit_user.connect(recv_perm_edit, sender=Cluster)
