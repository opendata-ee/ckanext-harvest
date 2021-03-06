# -*- coding: utf-8 -*-

import re
from lxml import etree
from lxml.etree import XMLSyntaxError
from pylons.i18n import _

import ckan.new_authz
from ckan import model
import ckan.plugins as p
from ckan.model.group import Group
import ckan.lib.helpers as h, json
from ckan.lib.base import BaseController, c, g, request, \
                          response, session, render, config, abort, redirect
from ckan.lib.navl.dictization_functions import DataError
from ckanext.harvest.logic.schema import harvest_source_form_schema
from ckanext.harvest.lib import HarvestError, pager_url
from ckanext.harvest.lib import HarvestNotice
from ckan.lib.helpers import Page

import logging
log = logging.getLogger(__name__)

class ViewController(BaseController):

    not_auth_message = _('Operatsioon pole lubatud')

    def _get_publishers(self):
        groups = None

        if ckan.new_authz.is_sysadmin(c.user):
            groups = Group.all(group_type='organization')
        elif c.userobj:
            groups = c.userobj.get_groups('organization')
        else: # anonymous user shouldn't have access to this page anyway.
            groups = []

        # Be explicit about which fields we make available in the template
        groups = [ {
            'name': g.name,
            'id': g.id,
            'title': g.title,
        } for g in groups ]

        return groups


    def index(self):
        context = {'model': model, 'user': c.user, 'session': model.Session,
                   'include_status': False}
        try:
            # Request all harvest sources
            c.sources = p.toolkit.get_action('harvest_source_list')(context,{})
        except p.toolkit.NotAuthorized,e:
            abort(401,self.not_auth_message)

        c.sources = sorted(c.sources,key=lambda source : source['publisher_title'])

        c.status = config.get('ckan.harvest.status')

        return render('index.html')

    def new(self,data = None,errors = None, error_summary = None):

        context = {'model': model, 'user': c.user or c.author,
                   'auth_user_obj': c.userobj}
        try:
            p.toolkit.check_access('harvest_source_create', context)
        except p.toolkit.NotAuthorized:
            abort(401, _('Operatsioon pole lubatud'))

        if ('save' in request.params) and not data:
            return self._save_new()

        # #1433 URL params pre-populate fields
        param_data = {}
        for field_name in ('url', 'type', 'title', 'description', 'publisher_id'):
            if field_name in request.params:
                param_data[field_name] = request.params[field_name]

        data = data or param_data or {}
        errors = errors or {}
        error_summary = error_summary or {}

        try:
            harvesters_info = p.toolkit.get_action('harvesters_info_show')(context,{})
        except p.toolkit.NotAuthorized,e:
            abort(401,self.not_auth_message)

        vars = {'data': data, 'errors': errors, 'error_summary': error_summary, 'harvesters': harvesters_info}

        c.groups = self._get_publishers()
        c.form = render('source/new_source_form.html', extra_vars=vars)
        return render('source/new.html')

    def _save_new(self):
        try:
            data_dict = dict(request.params)
            self._check_data_dict(data_dict)
            context = {'model':model, 'user':c.user, 'session':model.Session,
                       'schema':harvest_source_form_schema()}

            source = p.toolkit.get_action('harvest_source_create')(context,data_dict)
            context = {'model': model, 'user': c.user or c.author,
                       'auth_user_obj': c.userobj}

            # Create a harvest job for the new source
            p.toolkit.get_action('harvest_job_create')(context,{'source_id':source['id']})

            h.flash_success(u'Uus andmekorje allikas loodud ja lisatud andmekorje järjekorda!')
            redirect('/harvest/%s' % source['id'])
        except p.toolkit.NotAuthorized,e:
            abort(401,self.not_auth_message)
        except DataError,e:
            abort(400, 'Andmetervikluse viga')
        except p.toolkit.ValidationError,e:
            errors = e.error_dict
            error_summary = e.error_summary if hasattr(e,'error_summary') else None
            return self.new(data_dict, errors, error_summary)

    def edit(self, id, data = None,errors = None, error_summary = None):

        if ('save' in request.params) and not data:
            return self._save_edit(id)

        try:
            context = {'model':model, 'user':c.user, 'include_status':False}

            old_data = p.toolkit.get_action('harvest_source_show')(context, {'id':id})
        except p.toolkit.ObjectNotFound:
            abort(404, _('Andmekorja allikat ei leitud'))
        except p.toolkit.NotAuthorized:
            abort(401, self.not_auth_message)
        try:
            p.toolkit.check_access('harvest_source_update', context)
        except p.toolkit.NotAuthorized:
            abort(401, _('Operatsioon pole lubatud'))

        data = data or old_data
        errors = errors or {}
        error_summary = error_summary or {}
        try:
            context = {'model': model, 'user': c.user}
            harvesters_info = p.toolkit.get_action('harvesters_info_show')(context, {})
        except p.toolkit.NotAuthorized:
            abort(401, self.not_auth_message)

        vars = {'data': data, 'errors': errors, 'error_summary': error_summary, 'harvesters': harvesters_info}

        c.source_title = old_data.get('title') if old_data else ''
        c.source_id = id
        c.groups = self._get_publishers()
        c.form = render('source/new_source_form.html', extra_vars=vars)
        return render('source/edit.html')

    def _save_edit(self,id):
        try:
            data_dict = dict(request.params)
            data_dict['id'] = id
            self._check_data_dict(data_dict)
            context = {'model':model, 'user':c.user, 'session':model.Session,
                       'schema':harvest_source_form_schema()}

            source = p.toolkit.get_action('harvest_source_update')(context,data_dict)

            h.flash_success(_('Andmekorje allikas edukalt salvestatud.'))
            redirect('/harvest/%s' %id)
        except p.toolkit.NotAuthorized,e:
            abort(401,self.not_auth_message)
        except DataError,e:
            abort(400, _('Andmetervikluse viga'))
        except p.toolkit.ObjectNotFound, e:
            abort(404, _('Andmekorje allikat ei leitud'))
        except p.toolkit.ValidationError,e:
            errors = e.error_dict
            error_summary = e.error_summary if hasattr(e,'error_summary') else None
            return self.edit(id,data_dict, errors, error_summary)

    def _check_data_dict(self, data_dict):
        '''Check if the return data is correct'''
        surplus_keys_schema = ['id','publisher_id','user_id','config','save']
        schema_keys = harvest_source_form_schema().keys()
        keys_in_schema = set(schema_keys) - set(surplus_keys_schema)

        # user_id is not yet used, we'll set the logged user one for the time being
        if not data_dict.get('user_id',None):
            if c.userobj:
                data_dict['user_id'] = c.userobj.id
        if keys_in_schema - set(data_dict.keys()):
            err = "%s, %s" % (data_dict.keys(), keys_in_schema,)
            raise Exception(err)
            #log.info(_('Incorrect form fields posted'))
            #raise DataError(data_dict)

    def read(self,id):
        try:
            context = {'model':model, 'user':c.user,
                       'detailed': h.check_access('harvest_job_create', {'source_id':id})}
            c.source = p.toolkit.get_action('harvest_source_show')(context, {'id':id})

            c.page = Page(
                collection=c.source['status']['packages'],
                page=request.params.get('page', 1),
                items_per_page=20,
                url=pager_url
            )

            return render('source/read.html')
        except p.toolkit.ObjectNotFound:
            abort(404,_('Andmekorje allikat ei leitud'))
        except p.toolkit.NotAuthorized,e:
            abort(401,self.not_auth_message)



    def delete(self,id):
        try:
            context = {'model':model, 'user':c.user}
            p.toolkit.get_action('harvest_source_delete')(context, {'id':id})

            h.flash_success(_('Andmekorje allikas de-aktiveeritud!'))
            redirect(h.url_for('harvest'))
        except p.toolkit.ObjectNotFound:
            abort(404,_('Andmekorje allikat ei leitud'))
        except p.toolkit.NotAuthorized,e:
            abort(401,self.not_auth_message)


    def create_harvesting_job(self,id):
        try:
            context = {'model':model, 'user':c.user, 'session':model.Session}
            p.toolkit.get_action('harvest_job_create')(context,{'source_id':id})
            refresh_interval_min = config.get('ckan.harvest.refresh_interval_min', '15')
            h.flash_success(u'Allikas lisatud andmekorje järjekorda! Järgmine korje toimub mitte hiljem kui %s minuti pärast.' % refresh_interval_min)
        except p.toolkit.ObjectNotFound:
            abort(404,_('Andmekorje allikat ei leitud'))
        except p.toolkit.NotAuthorized,e:
            abort(401,self.not_auth_message)
        except HarvestError, e:
            msg = u'Viga allika lisamisel andmekorje järjekorda: %s' % unicode(e)
            h.flash_error(msg)
        except HarvestNotice, e:
            msg = u'Takistus allika lisamisel andmekorje järjekorda: %s' % unicode(e)
            h.flash_notice(msg)
        except Exception, e:
            msg = 'Tekkis viga: [%s]' % str(e)
            h.flash_error(msg)

        redirect(h.url_for('harvest'))

    def show_object(self,id):

        try:
            context = {'model':model, 'user':c.user}
            obj = p.toolkit.get_action('harvest_object_show')(context, {'id':id})

            # Check content type. It will probably be either XML or JSON
            try:
                content = re.sub('<\?xml(.*)\?>','', obj['content'])
                etree.fromstring(content)
                response.content_type = 'application/xml'
            except XMLSyntaxError:
                try:
                    json.loads(obj['content'])
                    response.content_type = 'application/json'
                except ValueError:
                    pass

            response.headers['Content-Length'] = len(obj['content'])
            return obj['content']
        except p.toolkit.ObjectNotFound:
            abort(404,_('Korjeobjekti ei leitud'))
        except p.toolkit.NotAuthorized,e:
            abort(401,self.not_auth_message)
        except Exception, e:
            msg = 'Tekkis viga: [%s]' % str(e)
            abort(500,msg)
