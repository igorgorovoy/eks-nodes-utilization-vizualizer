import time
import json
import boto3
from kubernetes import client, config
from colorama import Fore

aws_region = 'eu-west-1'
pricing_region = 'us-east-1'

ec2_client = boto3.client('ec2', region_name=aws_region)

config.load_kube_config(context="arn:aws:eks:eu-west-1:294949574448:cluster/dev-1-30")
v1 = client.CoreV1Api()

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
    price = get_instance_price(instance_type)
    return instance_type, price

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

def get_pod_cpu_usage(node):
    pods = v1.list_pod_for_all_namespaces(field_selector=f'spec.nodeName={node.metadata.name}').items
    total_cpu_usage = 0

    for pod in pods:
        if pod.spec.containers:
            for container in pod.spec.containers:
                resources = container.resources
                if resources.requests and 'cpu' in resources.requests:
                    cpu_request = resources.requests['cpu']
                    total_cpu_usage += int(cpu_request.replace('m', '')) / 1000 if 'm' in cpu_request else int(cpu_request)

    return total_cpu_usage  # Повертаємо в vCPUs

def get_pod_memory_usage(node):
    pods = v1.list_pod_for_all_namespaces(field_selector=f'spec.nodeName={node.metadata.name}').items
    total_memory_usage = 0

    for pod in pods:
        if pod.spec.containers:
            for container in pod.spec.containers:
                resources = container.resources
                if resources.requests and 'memory' in resources.requests:
                    memory_request = resources.requests['memory']
                    if memory_request.endswith('Mi'):
                        total_memory_usage += int(memory_request[:-2]) / 1024  # В GiB
                    elif memory_request.endswith('Gi'):
                        total_memory_usage += int(memory_request[:-2])  # В GiB

    return total_memory_usage  # Повертаємо в GiB

def get_node_utilization(node):
    cpu_allocatable_str = node.status.allocatable['cpu']
    cpu_allocatable = int(cpu_allocatable_str.replace('m', '')) / 1000 if 'm' in cpu_allocatable_str else int(cpu_allocatable_str)
    memory_allocatable = int(node.status.allocatable['memory'].replace('Ki', '')) / (1024 * 1024)

    cpu_capacity_str = node.status.capacity['cpu']
    cpu_capacity = int(cpu_capacity_str.replace('m', '')) / 1000 if 'm' in cpu_capacity_str else int(cpu_capacity_str)
    memory_capacity = int(node.status.capacity['memory'].replace('Ki', '')) / (1024 * 1024)

    # Отримуємо фактичну зайнятість
    used_cpu = get_pod_cpu_usage(node)
    used_memory = get_pod_memory_usage(node)

    cpu_utilization = (used_cpu / cpu_capacity) * 100 if cpu_capacity > 0 else 0
    memory_utilization = (used_memory / memory_capacity) * 100 if memory_capacity > 0 else 0
    return cpu_utilization, memory_utilization, cpu_capacity, memory_capacity

def get_node_status(node):
    statuses = []
    for condition in node.status.conditions:
        if condition.status == 'True':
            statuses.append(condition.type)
    if 'Ready' in statuses:
        return "Ready"
    else:
        return ", ".join(statuses) if statuses else "NotReady"

def get_node_type(node):
    if "node-role.kubernetes.io/master" in node.metadata.labels:
        return "Master"
    else:
        return "Worker"

def display_progress_bar(value):
    bar_length = 20  # Довжина прогрес-бару
    filled_length = int(bar_length * (value / 100))
    bar = '█' * filled_length + ' ' * (bar_length - filled_length)

    color = Fore.RED if value < 30 else Fore.YELLOW if value < 80 else Fore.GREEN
    return f"{color}[{bar}] {value:.2f}%{Fore.RESET}"

def analyze_nodes():
    while True:
        nodes = get_nodes()  # Оновлюємо список нодів на кожному циклі
        total_cpu_utilization = 0
        total_memory_utilization = 0
        total_cpu_capacity = 0
        total_memory_capacity = 0
        total_cost = 0.0
        node_count = 0
        node_data = []

        for node in nodes:
            instance_id = get_instance_id(node)
            if instance_id:
                instance_type, price = get_instance_details(instance_id)
                cpu_utilization, memory_utilization, cpu_capacity, memory_capacity = get_node_utilization(node)
                node_status = get_node_status(node)
                node_type = get_node_type(node)

                node_data.append({
                    "name": node.metadata.name,
                    "instance_type": instance_type,
                    "price": price,
                    "cpu_capacity": cpu_capacity,
                    "memory_capacity": memory_capacity,
                    "cpu_utilization": cpu_utilization,
                    "memory_utilization": memory_utilization,
                    "status": node_status,
                    "type": node_type
                })

                total_cpu_utilization += cpu_utilization
                total_memory_utilization += memory_utilization
                total_cpu_capacity += cpu_capacity
                total_memory_capacity += memory_capacity
                total_cost += price
                node_count += 1
            else:
                print(f"Error: Could not retrieve instance ID for node {node.metadata.name}")

        if node_count > 0:
            avg_cpu_utilization = total_cpu_utilization / node_count
            avg_memory_utilization = total_memory_utilization / node_count

            print("\n" + "-" * 160)
            print(
                f"{'Node Name':<30} | {'Instance Type':<20} | {'Node Pricing':<15} | {'CPU Capacity':<15} | {'Memory Capacity':<15} | {'CPU Utilization':<20} | {'Memory Utilization':<20} | {'Status':<10} | {'Node Type':<10}")
            print("-" * 160)
            for data in node_data:
                cpu_bar = display_progress_bar(data["cpu_utilization"])
                memory_bar = display_progress_bar(data["memory_utilization"])

                print(
                    f"{data['name']:<30} | {data['instance_type']:<20} | ${data['price']:.4f}/hour     | {data['cpu_capacity']:<15} | {data['memory_capacity']:<15} | {cpu_bar} | {memory_bar} | {data['status']:<10} | {data['type']:<10}")

            print("-" * 160)
            print(f"\nAverage CPU Utilization for all nodes: {avg_cpu_utilization:.2f}%")
            print(f"Average Memory Utilization for all nodes: {avg_memory_utilization:.2f}%")
            print(f"Total Nodes: {node_count}")
            print(f"Total CPU Capacity: {total_cpu_capacity:.2f} vCPUs")
            print(f"Total Memory Capacity: {total_memory_capacity:.2f} GiB")
            print(f"Total Cost for all nodes: ${total_cost:.4f}/hour")
        else:
            print("\nNo nodes found for utilization analysis.")

        print("\nPress Ctrl+C to quit...")
        time.sleep(5)

try:
    analyze_nodes()
except KeyboardInterrupt:
    print("\nExiting...")
