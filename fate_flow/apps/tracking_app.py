#
#  Copyright 2019 The FATE Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
from flask import Flask, request
from google.protobuf import json_format

from arch.api.utils.core import json_loads
from fate_flow.db.db_models import Job, DB
from fate_flow.manager.tracking import Tracking
from fate_flow.settings import stat_logger
from fate_flow.storage.fate_storage import FateStorage
from fate_flow.utils import job_utils, data_utils
from fate_flow.utils.api_utils import get_json_result
from federatedml.feature.instance import Instance

manager = Flask(__name__)


@manager.errorhandler(500)
def internal_server_error(e):
    stat_logger.exception(e)
    return get_json_result(retcode=100, retmsg=str(e))


@manager.route('/job/data_view', methods=['post'])
def job_view():
    request_data = request.json
    check_request_parameters(request_data)
    job_tracker = Tracking(job_id=request_data['job_id'], role=request_data['role'], party_id=request_data['party_id'])
    job_view_data = job_tracker.get_job_view()
    if job_view_data:
        job_metric_list = job_tracker.get_metric_list(job_level=True)
        job_view_data['model_summary'] = {}
        for metric_namespace, namespace_metrics in job_metric_list.items():
            job_view_data['model_summary'][metric_namespace] = job_view_data['model_summary'].get(metric_namespace, {})
            for metric_name in namespace_metrics:
                job_view_data['model_summary'][metric_namespace][metric_name] = job_view_data['model_summary'][
                    metric_namespace].get(metric_name, {})
                for metric_data in job_tracker.get_job_metric_data(metric_namespace=metric_namespace,
                                                                   metric_name=metric_name):
                    job_view_data['model_summary'][metric_namespace][metric_name][metric_data.key] = metric_data.value
        return get_json_result(retcode=0, retmsg='success', data=job_view_data)
    else:
        return get_json_result(retcode=101, retmsg='error')


@manager.route('/component/metrics', methods=['post'])
def component_metrics():
    request_data = request.json
    check_request_parameters(request_data)
    tracker = Tracking(job_id=request_data['job_id'], component_name=request_data['component_name'],
                       role=request_data['role'], party_id=request_data['party_id'])
    metrics = tracker.get_metric_list()
    if metrics:
        return get_json_result(retcode=0, retmsg='success', data=metrics)
    else:
        return get_json_result(retcode=0, retmsg='no data', data={})


@manager.route('/component/metric_data', methods=['post'])
def component_metric_data():
    request_data = request.json
    check_request_parameters(request_data)
    tracker = Tracking(job_id=request_data['job_id'], component_name=request_data['component_name'],
                       role=request_data['role'], party_id=request_data['party_id'])
    metric_data = tracker.get_metric_data(metric_namespace=request_data['metric_namespace'],
                                          metric_name=request_data['metric_name'])
    metric_meta = tracker.get_metric_meta(metric_namespace=request_data['metric_namespace'],
                                          metric_name=request_data['metric_name'])
    if metric_data:
        metric_data_list = [(metric.key, metric.value) for metric in metric_data]
        metric_data_list.sort(key=lambda x: x[0])
        return get_json_result(retcode=0, retmsg='success', data=metric_data_list,
                               meta=metric_meta.to_dict() if metric_meta else {})
    else:
        return get_json_result(retcode=0, retmsg='no data', data=[])


@manager.route('/component/parameters', methods=['post'])
def component_parameters():
    request_data = request.json
    check_request_parameters(request_data)
    job_id = request_data.get('job_id', '')
    job_dsl_parser = job_utils.get_job_dsl_parser_by_job_id(job_id=job_id)
    if job_dsl_parser:
        component = job_dsl_parser.get_component_info(request_data['component_name'])
        parameters = component.get_role_parameters()
        for role, partys_parameters in parameters.items():
            for party_parameters in partys_parameters:
                if party_parameters.get('local', {}).get('role', '') == request_data['role'] and party_parameters.get(
                        'local', {}).get('party_id', '') == request_data['party_id']:
                    output_parameters = {}
                    output_parameters['module'] = party_parameters.get('module', '')
                    for p_k, p_v in party_parameters.items():
                        if p_k.endswith('Param'):
                            output_parameters[p_k] = p_v
                    return get_json_result(retcode=0, retmsg='success', data=output_parameters)
        else:
            return get_json_result(retcode=102, retmsg='can not found this component parameters')
    else:
        return get_json_result(retcode=101, retmsg='can not found this job')


@manager.route('/component/output/model', methods=['post'])
def component_output_model():
    request_data = request.json
    check_request_parameters(request_data)
    job_runtime_conf = job_utils.get_job_runtime_conf(job_id=request_data['job_id'], role=request_data['role'],
                                                      party_id=request_data['party_id'])
    model_key = job_runtime_conf['job_parameters']['model_key']
    tracker = Tracking(job_id=request_data['job_id'], component_name=request_data['component_name'],
                       role=request_data['role'], party_id=request_data['party_id'], model_key=model_key)
    output_model = tracker.get_output_model()
    output_model_json = {}
    for buffer_name, buffer_object in output_model.items():
        if buffer_name.endswith('Param'):
            output_model_json = json_format.MessageToDict(buffer_object, including_default_value_fields=True)
    if output_model_json:
        pipeline_output_model = tracker.get_output_model_meta()
        this_component_model_meta = {}
        for k, v in pipeline_output_model.items():
            if k.endswith('_module_name'):
                if k == '{}_module_name'.format(request_data['component_name']):
                    this_component_model_meta['module_name'] = v
            else:
                k_i = k.split('.')
                if '.'.join(k_i[:-1]) == request_data['component_name']:
                    this_component_model_meta[k] = v
        return get_json_result(retcode=0, retmsg='success', data=output_model_json, meta=this_component_model_meta)
    else:
        return get_json_result(retcode=0, retmsg='no data', data={})


@manager.route('/component/output/data', methods=['post'])
def component_output_data():
    request_data = request.json
    check_request_parameters(request_data)
    tracker = Tracking(job_id=request_data['job_id'], component_name=request_data['component_name'],
                       role=request_data['role'], party_id=request_data['party_id'])
    job_dsl_parser = job_utils.get_job_dsl_parser_by_job_id(job_id=request_data['job_id'])
    if not job_dsl_parser:
        return get_json_result(retcode=101, retmsg='can not new parser', data=[])
    component = job_dsl_parser.get_component_info(request_data['component_name'])
    if not component:
        return get_json_result(retcode=102, retmsg='can found component', data=[])
    output_dsl = component.get_output()
    output_data_table = tracker.get_output_data_table(output_dsl.get('data')[0])
    output_data = []
    num = 100
    data_label = False
    if output_data_table:
        for k, v in output_data_table.collect():
            if num == 0:
                break
            l = [k]
            if isinstance(v, Instance):
                if v.label is not None:
                    l.append(v.label)
                    data_label = True
                l.extend(data_utils.dataset_to_list(v.features))
            else:
                l.extend(data_utils.dataset_to_list(v))
            output_data.append(l)
            num -= 1
    if output_data:
        output_data_meta = FateStorage.get_data_table_meta_by_instance(output_data_table)
        schema = output_data_meta.get('schema', {})
        header = [schema.get('sid_name', 'sid')]
        if data_label:
            header.append(schema.get('label_name'))
        header.extend(schema.get('header', []))
        return get_json_result(retcode=0, retmsg='success', data=output_data, meta={'header': header})
    else:
        return get_json_result(retcode=0, retmsg='no data', data=[])


@DB.connection_context()
def check_request_parameters(request_data):
    if 'role' not in request_data and 'party_id' not in request_data:
        jobs = Job.select(Job.f_runtime_conf).where(Job.f_job_id == request_data.get('job_id', ''),
                                                    Job.f_is_initiator == 1)
        if jobs:
            job = jobs[0]
            job_runtime_conf = json_loads(job.f_runtime_conf)
            job_initiator = job_runtime_conf.get('initiator', {})
            role = job_initiator.get('role', '')
            party_id = job_initiator.get('party_id', 0)
            request_data['role'] = role
            request_data['party_id'] = party_id
