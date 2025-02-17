# Copyright 2018 PerfKitBenchmarker Authors. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests for perfkitbenchmarker.providers.gcp.google_kubernetes_engine."""

# pylint: disable=not-context-manager

import os

import unittest
from absl import flags as flgs
import contextlib2
import mock

from perfkitbenchmarker import data
from perfkitbenchmarker import errors
from perfkitbenchmarker import vm_util
from perfkitbenchmarker.configs import benchmark_config_spec
from perfkitbenchmarker.providers.gcp import gce_network
from perfkitbenchmarker.providers.gcp import google_kubernetes_engine
from perfkitbenchmarker.providers.gcp import util
from tests import pkb_common_test_case
from six.moves import builtins
FLAGS = flgs.FLAGS

_COMPONENT = 'test_component'
_RUN_URI = 'fake-urn-uri'
_NVIDIA_DRIVER_SETUP_DAEMON_SET_SCRIPT = 'https://raw.githubusercontent.com/GoogleCloudPlatform/container-engine-accelerators/master/nvidia-driver-installer/cos/daemonset-preloaded.yaml'
_NVIDIA_UNRESTRICTED_PERMISSIONS_DAEMON_SET = 'nvidia_unrestricted_permissions_daemonset.yml'

_INSTANCE_GROUPS_LIST_OUTPUT = (
    '../../../tests/data/gcloud_compute_instance_groups_list_instances.json')
_NODE_POOLS_LIST_OUTPUT = (
    '../../../tests/data/gcloud_container_node_pools_list.json')


@contextlib2.contextmanager
def patch_critical_objects(stdout='', stderr='', return_code=0, flags=FLAGS):
  with contextlib2.ExitStack() as stack:
    flags.gcloud_path = 'gcloud'
    flags.run_uri = _RUN_URI
    flags.data_search_paths = ''

    stack.enter_context(mock.patch(builtins.__name__ + '.open'))
    stack.enter_context(mock.patch(vm_util.__name__ + '.PrependTempDir'))
    stack.enter_context(mock.patch(vm_util.__name__ + '.NamedTemporaryFile'))
    stack.enter_context(
        mock.patch(
            util.__name__ + '.GetDefaultProject', return_value='fakeproject'))
    stack.enter_context(
        mock.patch(
            util.__name__ + '.GetDefaultUser', return_value='fakeuser'))
    stack.enter_context(
        mock.patch(
            gce_network.__name__ + '.GceFirewall.GetFirewall',
            return_value='fakefirewall'))
    stack.enter_context(
        mock.patch(
            gce_network.__name__ + '.GceNetwork.GetNetwork',
            return_value=gce_network.GceNetwork(
                gce_network.GceNetworkSpec('fakeproject'))))

    retval = (stdout, stderr, return_code)
    issue_command = stack.enter_context(
        mock.patch(vm_util.__name__ + '.IssueCommand', return_value=retval))
    yield issue_command


class GoogleKubernetesEngineCustomMachineTypeTestCase(
    pkb_common_test_case.PkbCommonTestCase):

  @staticmethod
  def create_kubernetes_engine_spec():
    kubernetes_engine_spec = benchmark_config_spec._ContainerClusterSpec(
        'NAME', **{
            'cloud': 'GCP',
            'vm_spec': {
                'GCP': {
                    'machine_type': {
                        'cpus': 4,
                        'memory': '1024MiB',
                    },
                },
            },
        })
    return kubernetes_engine_spec

  def testCreate(self):
    spec = self.create_kubernetes_engine_spec()
    with patch_critical_objects() as issue_command:
      cluster = google_kubernetes_engine.GkeCluster(spec)
      cluster._Create()
      command_string = ' '.join(issue_command.call_args[0][0])

      self.assertEqual(issue_command.call_count, 1)
      self.assertIn('gcloud container clusters create', command_string)
      self.assertIn('--machine-type custom-4-1024', command_string)


class GoogleKubernetesEngineTestCase(pkb_common_test_case.PkbCommonTestCase):

  @staticmethod
  def create_kubernetes_engine_spec():
    kubernetes_engine_spec = benchmark_config_spec._ContainerClusterSpec(
        'NAME', **{
            'cloud': 'GCP',
            'vm_spec': {
                'GCP': {
                    'machine_type': 'fake-machine-type',
                    'zone': 'us-central1-a',
                    'min_cpu_platform': 'skylake',
                    'boot_disk_type': 'foo',
                    'boot_disk_size': 200,
                    'num_local_ssds': 2,
                },
            },
            'vm_count': 2,
        })
    return kubernetes_engine_spec

  def testCreate(self):
    spec = self.create_kubernetes_engine_spec()
    with patch_critical_objects() as issue_command:
      cluster = google_kubernetes_engine.GkeCluster(spec)
      cluster._Create()
      command_string = ' '.join(issue_command.call_args[0][0])

      self.assertEqual(issue_command.call_count, 1)
      self.assertIn('gcloud container clusters create', command_string)
      self.assertIn('--num-nodes 2', command_string)
      self.assertIn('--machine-type fake-machine-type', command_string)
      self.assertIn('--zone us-central1-a', command_string)
      self.assertIn('--min-cpu-platform skylake', command_string)
      self.assertIn('--disk-size 200', command_string)
      self.assertIn('--disk-type foo', command_string)
      self.assertIn('--local-ssd-count 2', command_string)

  def testCreateQuotaExceeded(self):
    spec = self.create_kubernetes_engine_spec()
    with patch_critical_objects(
        stderr="""
        message=Insufficient regional quota to satisfy request: resource "CPUS":
        request requires '6400.0' and is short '5820.0'""",
        return_code=1) as issue_command:
      cluster = google_kubernetes_engine.GkeCluster(spec)
      with self.assertRaises(
          errors.Benchmarks.QuotaFailure):
        cluster._Create()
      self.assertEqual(issue_command.call_count, 1)

  def testCreateResourcesExhausted(self):
    spec = self.create_kubernetes_engine_spec()
    with patch_critical_objects(
        stderr="""
        [ZONE_RESOURCE_POOL_EXHAUSTED_WITH_DETAILS]:
        Instance 'test' creation failed: The zone
        'projects/artemis-prod/zones/us-central1-a' does not have enough
        resources available to fulfill the request.""",
        return_code=1) as issue_command:
      cluster = google_kubernetes_engine.GkeCluster(spec)
      with self.assertRaises(
          errors.Benchmarks.InsufficientCapacityCloudFailure):
        cluster._Create()
      self.assertEqual(issue_command.call_count, 1)

  def testPostCreate(self):
    spec = self.create_kubernetes_engine_spec()
    with patch_critical_objects() as issue_command:
      cluster = google_kubernetes_engine.GkeCluster(spec)
      cluster._PostCreate()
      command_string = ' '.join(issue_command.call_args[0][0])

      self.assertEqual(issue_command.call_count, 1)
      self.assertIn(
          'gcloud container clusters get-credentials pkb-{0}'.format(_RUN_URI),
          command_string)
      self.assertIn('KUBECONFIG', issue_command.call_args[1]['env'])

  def testDelete(self):
    spec = self.create_kubernetes_engine_spec()
    with patch_critical_objects() as issue_command:
      cluster = google_kubernetes_engine.GkeCluster(spec)
      cluster._Delete()
      command_string = ' '.join(issue_command.call_args[0][0])

      self.assertEqual(issue_command.call_count, 1)
      self.assertIn('gcloud container clusters delete pkb-{0}'.format(_RUN_URI),
                    command_string)
      self.assertIn('--zone us-central1-a', command_string)

  def testExists(self):
    spec = self.create_kubernetes_engine_spec()
    with patch_critical_objects() as issue_command:
      cluster = google_kubernetes_engine.GkeCluster(spec)
      cluster._Exists()
      command_string = ' '.join(issue_command.call_args[0][0])

      self.assertEqual(issue_command.call_count, 1)
      self.assertIn(
          'gcloud container clusters describe pkb-{0}'.format(_RUN_URI),
          command_string)

  def testGetResourceMetadata(self):
    spec = self.create_kubernetes_engine_spec()
    with patch_critical_objects() as issue_command:
      cluster = google_kubernetes_engine.GkeCluster(spec)
      metadata = cluster.GetResourceMetadata()
      self.assertEqual(issue_command.call_count, 0)
      self.assertContainsSubset(
          {
              'project': 'fakeproject',
              'gce_local_ssd_count': 2,
              'gce_local_ssd_interface': 'SCSI',
              'machine_type': 'fake-machine-type',
              'boot_disk_type': 'foo',
              'boot_disk_size': 200,
              'cloud': 'GCP',
              'cluster_type': 'Kubernetes',
              'zone': 'us-central1-a',
              'size': 2,
              'container_cluster_version': 'latest'
          }, metadata)


class GoogleKubernetesEngineAutoscalingTestCase(
    pkb_common_test_case.PkbCommonTestCase):

  @staticmethod
  def create_kubernetes_engine_spec():
    kubernetes_engine_spec = benchmark_config_spec._ContainerClusterSpec(
        'NAME', **{
            'cloud': 'GCP',
            'vm_spec': {
                'GCP': {
                    'machine_type': 'fake-machine-type',
                    'zone': 'us-central1-a',
                },
            },
            'min_vm_count': 1,
            'vm_count': 2,
            'max_vm_count': 3,
        })
    return kubernetes_engine_spec

  def testCreate(self):
    spec = self.create_kubernetes_engine_spec()
    with patch_critical_objects() as issue_command:
      cluster = google_kubernetes_engine.GkeCluster(spec)
      cluster._Create()
      command_string = ' '.join(issue_command.call_args[0][0])

      self.assertEqual(issue_command.call_count, 1)
      self.assertIn('gcloud container clusters create', command_string)
      self.assertIn('--enable-autoscaling', command_string)
      self.assertIn('--min-nodes 1', command_string)
      self.assertIn('--num-nodes 2', command_string)
      self.assertIn('--max-nodes 3', command_string)

  def testGetResourceMetadata(self):
    spec = self.create_kubernetes_engine_spec()
    with patch_critical_objects() as issue_command:
      cluster = google_kubernetes_engine.GkeCluster(spec)
      metadata = cluster.GetResourceMetadata()
      self.assertEqual(issue_command.call_count, 0)
      self.assertContainsSubset(
          {
              'project': 'fakeproject',
              'cloud': 'GCP',
              'cluster_type': 'Kubernetes',
              'min_size': 1,
              'size': 2,
              'max_size': 3
          }, metadata)


class GoogleKubernetesEngineVersionFlagTestCase(
    pkb_common_test_case.PkbCommonTestCase):

  @staticmethod
  def create_kubernetes_engine_spec():
    kubernetes_engine_spec = benchmark_config_spec._ContainerClusterSpec(
        'NAME', **{
            'cloud': 'GCP',
            'vm_spec': {
                'GCP': {
                    'machine_type': 'fake-machine-type',
                },
            },
        })
    return kubernetes_engine_spec

  def testCreateCustomVersion(self):
    spec = self.create_kubernetes_engine_spec()
    FLAGS.container_cluster_version = 'fake-version'
    with patch_critical_objects() as issue_command:
      cluster = google_kubernetes_engine.GkeCluster(spec)
      cluster._Create()
      command_string = ' '.join(issue_command.call_args[0][0])

      self.assertEqual(issue_command.call_count, 1)
      self.assertIn('--cluster-version fake-version', command_string)

  def testCreateDefaultVersion(self):
    spec = self.create_kubernetes_engine_spec()
    with patch_critical_objects() as issue_command:
      cluster = google_kubernetes_engine.GkeCluster(spec)
      cluster._Create()
      command_string = ' '.join(issue_command.call_args[0][0])

      self.assertEqual(issue_command.call_count, 1)
      self.assertIn('--cluster-version latest', command_string)


class GoogleKubernetesEngineWithGpusTestCase(
    pkb_common_test_case.PkbCommonTestCase):

  @staticmethod
  def create_kubernetes_engine_spec():
    kubernetes_engine_spec = benchmark_config_spec._ContainerClusterSpec(
        'NAME', **{
            'cloud': 'GCP',
            'vm_spec': {
                'GCP': {
                    'machine_type': 'fake-machine-type',
                    'gpu_type': 'k80',
                    'gpu_count': 2,
                },
            },
            'vm_count': 2,
        })
    return kubernetes_engine_spec

  def testCreate(self):
    spec = self.create_kubernetes_engine_spec()
    with patch_critical_objects() as issue_command:
      cluster = google_kubernetes_engine.GkeCluster(spec)
      cluster._Create()
      command_string = ' '.join(issue_command.call_args[0][0])

      self.assertEqual(issue_command.call_count, 1)
      self.assertIn('gcloud container clusters create', command_string)
      self.assertIn('--num-nodes 2', command_string)
      self.assertIn('--machine-type fake-machine-type', command_string)
      self.assertIn('--accelerator type=nvidia-tesla-k80,count=2',
                    command_string)

  @mock.patch('perfkitbenchmarker.kubernetes_helper.CreateFromFile')
  def testPostCreate(self, create_from_file_patch):
    spec = self.create_kubernetes_engine_spec()
    with patch_critical_objects() as issue_command:
      cluster = google_kubernetes_engine.GkeCluster(spec)
      cluster._PostCreate()
      command_string = ' '.join(issue_command.call_args[0][0])

      self.assertEqual(issue_command.call_count, 1)
      self.assertIn(
          'gcloud container clusters get-credentials pkb-{0}'.format(_RUN_URI),
          command_string)
      self.assertIn('KUBECONFIG', issue_command.call_args[1]['env'])

      expected_args_to_create_from_file = (
          _NVIDIA_DRIVER_SETUP_DAEMON_SET_SCRIPT,
          data.ResourcePath(
              _NVIDIA_UNRESTRICTED_PERMISSIONS_DAEMON_SET)
      )
      expected_calls = [mock.call(arg)
                        for arg in expected_args_to_create_from_file]

      # Assert that create_from_file was called twice,
      # and that the args were as expected (should be the NVIDIA
      # driver setup daemon set, followed by the
      # NVIDIA unrestricted permissions daemon set.
      create_from_file_patch.assert_has_calls(expected_calls)


class GoogleKubernetesEngineGetNodesTestCase(GoogleKubernetesEngineTestCase):

  def testGetInstancesFromInstanceGroups(self):
    instance_group_name = 'gke-pkb-0c47e6fa-default-pool-167d73ee-grp'
    path = os.path.join(os.path.dirname(__file__), _INSTANCE_GROUPS_LIST_OUTPUT)
    output = open(path).read()
    spec = self.create_kubernetes_engine_spec()
    with patch_critical_objects(stdout=output) as issue_command:
      cluster = google_kubernetes_engine.GkeCluster(spec)
      instances = cluster._GetInstancesFromInstanceGroup(instance_group_name)

      command_string = ' '.join(issue_command.call_args[0][0])
      self.assertEqual(issue_command.call_count, 1)
      self.assertIn(
          'gcloud compute instance-groups list-instances '
          'gke-pkb-0c47e6fa-default-pool-167d73ee-grp', command_string)

      expected = set([
          'gke-pkb-0c47e6fa-default-pool-167d73ee-hmwk',
          'gke-pkb-0c47e6fa-default-pool-167d73ee-t854'
      ])
      self.assertEqual(expected, set(instances))  # order doesn't matter

  def testGetInstanceGroups(self):
    path = os.path.join(os.path.dirname(__file__), _NODE_POOLS_LIST_OUTPUT)
    output = open(path).read()
    spec = self.create_kubernetes_engine_spec()
    with patch_critical_objects(stdout=output) as issue_command:
      cluster = google_kubernetes_engine.GkeCluster(spec)
      instance_groups = cluster._GetInstanceGroups()

      command_string = ' '.join(issue_command.call_args[0][0])
      self.assertEqual(issue_command.call_count, 1)
      self.assertIn('gcloud container node-pools list', command_string)
      self.assertIn('--cluster', command_string)

      expected = set([
          'gke-pkb-0c47e6fa-default-pool-167d73ee-grp',
          'gke-pkb-0c47e6fa-test-efea7796-grp'
      ])
      self.assertEqual(expected, set(instance_groups))  # order doesn't matter


if __name__ == '__main__':
  unittest.main()
