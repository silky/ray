"""Utilities for e2e tests of KubeRay/Ray integration.
For consistency, all K8s interactions use kubectl through subprocess calls.
"""
import logging
from pathlib import Path
import subprocess
import time
from typing import Any, Dict, List, Optional
import yaml

logger = logging.getLogger(__name__)


def wait_for_crd(crd_name: str, tries=60, backoff_s=5):
    """CRD creation can take a bit of time after the client request.
    This function waits until the crd with the provided name is registered.
    """
    for i in range(tries):
        get_crd_output = subprocess.check_output(["kubectl", "get", "crd"]).decode()
        if crd_name in get_crd_output:
            logger.info(f"Confirmed existence of CRD {crd_name}.")
            return
        elif i < tries - 1:
            logger.info(f"Still waiting to register CRD {crd_name}")
            time.sleep(backoff_s)
        else:
            raise Exception(f"Failed to register CRD {crd_name}")


def wait_for_pods(goal_num_pods: int, namespace: str, tries=60, backoff_s=5) -> None:
    """Wait for the number of pods in the `namespace` to be exactly `num_pods`.

    Raise an exception after exceeding `tries` attempts with `backoff_s` second waits.
    """
    for i in range(tries):

        cur_num_pods = _get_num_pods(namespace)
        if cur_num_pods == goal_num_pods:
            logger.info(f"Confirmed {goal_num_pods} pod(s) in namespace {namespace}.")
            return
        elif i < tries - 1:
            logger.info(
                f"The number of pods in namespace {namespace} is {cur_num_pods}."
                f" Waiting until the number of pods is {goal_num_pods}."
            )
            time.sleep(backoff_s)
        else:
            raise Exception(
                f"Failed to scale to {goal_num_pods} pod(s) in namespace {namespace}."
            )


def _get_num_pods(namespace: str) -> int:
    return len(get_pod_names(namespace))


def get_pod_names(namespace: str) -> List[str]:
    """Get the list of pod names in the namespace."""
    get_pods_output = (
        subprocess.check_output(
            [
                "kubectl",
                "-n",
                namespace,
                "get",
                "pods",
                "-o",
                "custom-columns=POD:metadata.name",
                "--no-headers",
            ]
        )
        .decode()
        .strip()
    )

    # If there aren't any pods, the output is any empty string.
    if not get_pods_output:
        return []
    else:
        return get_pods_output.split("\n")
    pass


def wait_for_pod_to_start(
    pod_name_filter: str, namespace: str, tries=60, backoff_s=5
) -> None:
    """Waits for a pod to have Running status.phase.

    More precisely, waits until there is a pod with name containing `pod_name_filter`
    and the pod has Running status.phase."""
    for i in range(tries):
        pod = get_pod(pod_name_filter=pod_name_filter, namespace=namespace)
        if not pod:
            # We didn't get a matching pod.
            continue
        pod_status = (
            subprocess.check_output(
                [
                    "kubectl",
                    "-n",
                    namespace,
                    "get",
                    "pod",
                    pod,
                    "-o",
                    "custom-columns=POD:status.phase",
                    "--no-headers",
                ]
            )
            .decode()
            .strip()
        )
        # "not found" is part of the kubectl output if the pod's not there.
        if "not found" in pod_status:
            raise Exception(f"Pod {pod} not found.")
        elif pod_status == "Running":
            logger.info(f"Confirmed pod {pod} is Running.")
            return
        elif i < tries - 1:
            logger.info(
                f"Pod {pod} has status {pod_status}. Waiting for the pod to enter "
                "Running status."
            )
            time.sleep(backoff_s)
        else:
            raise Exception(f"Timed out waiting for pod {pod} to enter Running status.")


def wait_for_ray_health(
    pod_name_filter: str,
    namespace: str,
    tries=60,
    backoff_s=5,
    ray_container="ray-head",
) -> None:
    """Waits until a Ray pod passes `ray health-check`.

    More precisely, waits until a Ray pod whose name includes the string
    `pod_name_filter` passes `ray health-check`.
    (Ensures Ray has completely started in the pod.)

    Use case: Wait until there is a Ray head pod with Ray running on it.
    """
    for i in range(tries):
        try:
            pod = get_pod(pod_name_filter=pod_name_filter, namespace="default")
            assert pod, f"Couldn't find a pod matching {pod_name_filter}."
            # `ray health-check` yields 0 exit status iff it succeeds
            kubectl_exec(
                ["ray", "health-check"], pod, namespace, container=ray_container
            )
            logger.info(f"ray health check passes for pod {pod}")
            return
        except subprocess.CalledProcessError as e:
            logger.info(f"Failed ray health check for pod {pod}.")
            if i < tries - 1:
                logger.info("Trying again.")
                time.sleep(backoff_s)
            else:
                logger.info("Giving up.")
                raise e from None


def get_pod(pod_name_filter: str, namespace: str) -> Optional[str]:
    """Gets pods in the `namespace`.

    Returns the first pod that has `pod_name_filter` as a
    substring of its name. Returns None if there are no matches.
    """
    pod_names = get_pod_names(namespace)
    matches = [pod_name for pod_name in pod_names if pod_name_filter in pod_name]
    if not matches:
        logger.warning(f"No match for `{pod_name_filter}` in namespace `{namespace}`.")
        return None
    return matches[0]


def kubectl_exec(
    command: List[str],
    pod: str,
    namespace: str,
    container: Optional[str] = None,
) -> str:
    """kubectl exec the `command` in the given `pod` in the given `namespace`.
    If a `container` is specified, will specify that container for kubectl.

    Prints and return kubectl's output as a string.
    """
    container_option = ["-c", container] if container else []
    kubectl_exec_command = (
        ["kubectl", "exec", "-it", pod] + container_option + ["--"] + command
    )
    out = subprocess.check_output(kubectl_exec_command).decode().strip()
    # Print for debugging convenience.
    print(out)
    return out


def kubectl_exec_python_script(
    script_name: str,
    pod: str,
    namespace: str,
    container: Optional[str] = None,
) -> str:
    """
    Runs a python script in a container via `kubectl exec`.
    Scripts live in `tests/kuberay/scripts`.

    Prints and return kubectl's output as a string.
    """
    script_path = Path(__file__).resolve().parent / "scripts" / script_name
    with open(script_path) as script_file:
        script_string = script_file.read()
    return kubectl_exec(["python", "-c", script_string], pod, namespace, container)


def get_raycluster(raycluster: str, namespace: str) -> Dict[str, Any]:
    """Gets the Ray CR with name `raycluster` in namespace `namespace`.

    Returns the CR as a nested Dict.
    """
    get_raycluster_output = (
        subprocess.check_output(
            ["kubectl", "-n", namespace, "get", "raycluster", raycluster, "-o", "yaml"]
        )
        .decode()
        .strip()
    )
    return yaml.safe_load(get_raycluster_output)
