import time
import json
import boto3
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from colorama import Fore

aws_region = 'eu-west-1'
pricing_region = 'us-east-1'

ec2_client = boto3.client('ec2', region_name=aws_region)
###
# Завантажуємо конфігурацію Kubernetes
##
config.load_kube_config(context="arn:aws:eks:eu-west-1:294949574448:cluster/dev-1-30")
v1 = client.CoreV1Api()
metrics_api = client.CustomObjectsApi()  # Використовуємо CustomObjectsApi для отримання метрик

def get_nodes():
    return v1.list_node().items

def get_instance_id(node):
    annotations = node.metadata.annotations
    if 'node.kubernetes.io/instance-id' in annotations:
        return annotations['node.kubernetes.io/instance-id']
    else:
        return get_instance_id_by_internal_ip(node)

def get_instance_id_by_internal_ip(node):
    for addr in node.status.addresses:
        if addr.type == "InternalIP":
            internal_ip = addr.address
            response = ec2_client.describe_instances(
                Filters=[{
                    'Name': 'private-ip-address',
                    'Values': [internal_ip]
                }]
            )
            if response['Reservations']:
                return response['Reservations'][0]['Instances'][0]['InstanceId']
    return None

def get_instance_details(instance_id):
    instance_details = ec2_client.describe_instances(InstanceIds=[instance_id])
    instance_type = instance_details['Reservations'][0]['Instances'][0]['InstanceType']
    instance_lifecycle = instance_details['Reservations'][0]['Instances'][0].get('InstanceLifecycle', 'On-Demand')
    instance_status = 'Spot' if instance_lifecycle == 'spot' else 'On-Demand'
    price = get_instance_price(instance_type)
    return instance_type, price, instance_status

def get_instance_price(instance_type):
    pricing_client = boto3.client('pricing', region_name=pricing_region)
    response = pricing_client.get_products(
        ServiceCode='AmazonEC2',
        Filters=[
            {'Type': 'TERM_MATCH', 'Field': 'instanceType', 'Value': instance_type},
            {'Type': 'TERM_MATCH', 'Field': 'location', 'Value': 'EU (Ireland)'}
        ]
    )

    if 'PriceList' in response and response['PriceList']:
        for price_item in response['PriceList']:
            price_item_dict = json.loads(price_item)
            if 'terms' in price_item_dict:
                price_dimensions = price_item_dict['terms']['OnDemand']
                for key in price_dimensions:
                    price = price_dimensions[key]['priceDimensions']
                    for price_key in price:
                        return float(price[price_key]['pricePerUnit']['USD'])
    return 0.0

def get_pod_metrics(namespace="default"):
    """Отримуємо метрики подів з CustomObjectsApi."""
    try:
        return metrics_api.list_namespaced_custom_object(
            group="metrics.k8s.io",
            version="v1beta1",
            namespace=namespace,
            plural="pods"
        )
    except ApiException as e:
        print(f"Error fetching pod metrics: {e}")
        return None

def get_pod_metrics_all_namespaces():
    """Отримуємо метрики подів з CustomObjectsApi для всіх неймспейсів."""
    try:
        return metrics_api.list_cluster_custom_object(
            group="metrics.k8s.io",
            version="v1beta1",
            plural="pods"
        )
    except ApiException as e:
        print(f"Error fetching pod metrics: {e}")
        return None

def convert_memory_to_gib(memory_str):
    """Конвертує рядок пам'яті у GiB."""
    if memory_str.endswith('Gi'):
        return float(memory_str[:-2])  # В GiB
    elif memory_str.endswith('Mi'):
        return float(memory_str[:-2]) / 1024  # Конвертуємо MiB в GiB
    elif memory_str.endswith('Ki'):
        return float(memory_str[:-2]) / (1024 ** 2)  # Конвертуємо KiB в GiB
    else:
        raise ValueError(f"Unknown memory unit: {memory_str}")

def get_real_cpu_usage_all_namespaces():
    """Отримуємо реальне використання CPU для всіх подів у всіх неймспейсах."""
    pod_metrics = get_pod_metrics_all_namespaces()
    total_cpu_usage = 0

    if pod_metrics:
        for pod in pod_metrics['items']:
            for container in pod['containers']:
                cpu_usage = container['usage']['cpu']
                if cpu_usage.endswith('m'):
                    cpu_usage_milli = int(cpu_usage[:-1])  # В міліядрах
                    total_cpu_usage += cpu_usage_milli / 1000  # Конвертуємо в vCPUs
                elif cpu_usage.endswith('n'):
                    cpu_usage_nano = int(cpu_usage[:-1]) / 1_000_000_000  # Конвертуємо з нано в vCPUs
                    total_cpu_usage += cpu_usage_nano
                elif cpu_usage.endswith('u'):
                    cpu_usage_micro = int(cpu_usage[:-1]) / 1_000_000  # Конвертуємо з мікро в vCPUs
                    total_cpu_usage += cpu_usage_micro
                else:
                    total_cpu_usage += int(cpu_usage)  # Якщо це просто ядра, то без змін

    return total_cpu_usage

def get_real_memory_usage_all_namespaces():
    """Отримуємо реальне використання пам'яті для всіх подів у всіх неймспейсах."""
    pod_metrics = get_pod_metrics_all_namespaces()
    total_memory_usage = 0

    if pod_metrics:
        for pod in pod_metrics['items']:
            for container in pod['containers']:
                print (container)
                memory_usage = container['usage']['memory']
                if memory_usage.endswith('Gi'):
                    total_memory_usage += int(memory_usage[:-2])  # В GiB
                elif memory_usage.endswith('Mi'):
                    total_memory_usage += int(memory_usage[:-2]) / 1024  # В GiB
                elif memory_usage.endswith('Ki'):
                    total_memory_usage += int(memory_usage[:-2]) / (1024 ** 2)  # В GiB
    return total_memory_usage  # Повертаємо в GiB

# def get_pod_cpu_usage(node):
#     pods = v1.list_pod_for_all_namespaces(field_selector=f'spec.nodeName={node.metadata.name}').items
#     total_cpu_usage = 0
#
#     for pod in pods:
#         print(f"Pod Name: {pod.metadata.name}, Namespace: {pod.metadata.namespace}")  # Друкуємо назву та неймспейс пода
#         if pod.spec.containers:
#             for container in pod.spec.containers:
#                 resources = container.resources
#                 if resources.requests and 'cpu' in resources.requests:
#                     cpu_request = resources.requests['cpu']
#                     total_cpu_usage += int(cpu_request[:-1]) / 1000 if cpu_request.endswith('m') else int(cpu_request)  # Конвертуємо в vCPUs
#
#     print(f"Total CPU Usage for Node {node.metadata.name}: {total_cpu_usage:.2f} vCPUs")  # Друкуємо загальне використання CPU
#     return total_cpu_usage  # Повертаємо в vCPUs

# def get_real_memory_usage(node):
#     pods = v1.list_pod_for_all_namespaces(field_selector=f'spec.nodeName={node.metadata.name}').items
#     total_memory_usage = 0
#
#     for pod in pods:
#         print(f"Pod Name: {pod.metadata.name}, Namespace: {pod.metadata.namespace}")  # Друкуємо назву та неймспейс пода
#         if pod.spec.containers:
#             for container in pod.spec.containers:
#                 resources = container.resources
#                 if resources.requests and 'memory' in resources.requests:
#                     memory_request = resources.requests['memory']
#                     total_memory_usage += convert_memory_to_gib(memory_request)  # Конвертуємо пам'ять до GiB
#
#     print(f"Total Memory Usage for Node {node.metadata.name}: {total_memory_usage:.2f} GiB")  # Друкуємо загальне використання пам'яті
#     return total_memory_usage  # Повертаємо в GiB

def get_node_utilization(node):
    cpu_capacity = float(node.status.allocatable['cpu'].replace('m', '')) / 1000  # Конвертуємо в vCPUs
    memory_capacity = convert_memory_to_gib(node.status.allocatable['memory'])  # Конвертуємо в GiB
    real_cpu_usage = get_real_cpu_usage_all_namespaces()  # Отримуємо реальне споживання CPU
    real_memory_usage = get_real_memory_usage_all_namespaces()  # Отримуємо реальне споживання пам'яті

    print ("cpu_capacity "+str(cpu_capacity))
    print("memory_capacity " + str(memory_capacity))
    print("real_cpu_usage " + str(real_cpu_usage))
    print("real_memory_usage " + str(real_memory_usage))

    cpu_utilization = (real_cpu_usage / cpu_capacity) * 100 if cpu_capacity > 0 else 0
    memory_utilization = (real_memory_usage / memory_capacity) * 100 if memory_capacity > 0 else 0

    return cpu_utilization, memory_utilization, cpu_capacity, memory_capacity, real_cpu_usage, real_memory_usage

def display_progress_bar(value):
    bar_length = 30
    block = int(round(bar_length * value / 100))
    bar = "#" * block + "-" * (bar_length - block)
    color = Fore.GREEN if value < 80 else Fore.YELLOW if value < 90 else Fore.RED
    print(f'\r{color}[{bar}] {value:.2f}%', end='')

def analyze_nodes():
    nodes = get_nodes()
    for node in nodes:
        instance_id = get_instance_id(node)
        instance_type, price, instance_status = get_instance_details(instance_id)

        cpu_utilization, memory_utilization, cpu_capacity, memory_capacity, real_cpu_usage, real_memory_usage = get_node_utilization(node)

        print(f"\nNode Name: {node.metadata.name}")
        print(f"Instance ID: {instance_id}, Instance Type: {instance_type}, Price: {price:.4f} USD/hour, Status: {instance_status}")
        print(f"CPU Utilization: {cpu_utilization:.2f}% (Used: {real_cpu_usage:.2f} vCPUs, Capacity: {cpu_capacity:.2f} vCPUs)")
        print(f"Memory Utilization: {memory_utilization:.2f}% (Used: {real_memory_usage:.2f} GiB, Capacity: {memory_capacity:.2f} GiB)")
        # display_progress_bar(cpu_utilization)

if __name__ == "__main__":
    while True:
        analyze_nodes()
        time.sleep(60)  # Затримка перед наступним аналізом
