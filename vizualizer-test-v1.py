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


def get_pod_resource_requests(node):
    pods = v1.list_pod_for_all_namespaces(field_selector=f'spec.nodeName={node.metadata.name}').items
    total_requests_cpu = 0
    total_requests_memory = 0

    for pod in pods:
        if pod.spec.containers:
            for container in pod.spec.containers:
                resources = container.resources
                if resources and resources.requests:  # Check if resources and requests are not None
                    cpu_request = resources.requests.get('cpu', '0')
                    memory_request = resources.requests.get('memory', '0')

                    # Process CPU requests
                    if cpu_request:
                        try:
                            total_requests_cpu += int(cpu_request[:-1]) / 1000 if cpu_request.endswith('m') else int(
                                cpu_request)
                        except ValueError:
                            print(f"Warning: Invalid CPU request value '{cpu_request}' for pod '{pod.metadata.name}'")

                    # Process memory requests
                    if memory_request:
                        try:
                            total_requests_memory += int(memory_request[:-2]) if memory_request.endswith('Mi') else int(
                                memory_request[:-2]) * 1024
                        except ValueError:
                            print(
                                f"Warning: Invalid Memory request value '{memory_request}' for pod '{pod.metadata.name}'")

    return total_requests_cpu, total_requests_memory  # Return in vCPUs and GiB


def get_pod_cpu_usage(node):
    pods = v1.list_pod_for_all_namespaces(field_selector=f'spec.nodeName={node.metadata.name}').items
    total_cpu_usage = 0

    for pod in pods:
        if pod.spec.containers:
            for container in pod.spec.containers:
                if container.resources and container.resources.limits:  # Check if resources and limits are not None
                    cpu_usage = container.resources.limits.get('cpu', '0')
                    if cpu_usage:  # Ensure cpu_usage is not empty
                        try:
                            if cpu_usage == '0':
                                continue  # Ignore zero usage
                            total_cpu_usage += int(cpu_usage[:-1]) / 1000  # Convert from "m" to vCPUs
                        except ValueError:
                            print(f"Warning: Invalid CPU usage value '{cpu_usage}' for pod '{pod.metadata.name}'")
    return total_cpu_usage  # Return in vCPUs


def get_pod_memory_usage(node):
    pods = v1.list_pod_for_all_namespaces(field_selector=f'spec.nodeName={node.metadata.name}').items
    total_memory_usage = 0

    for pod in pods:
        if pod.spec.containers:
            for container in pod.spec.containers:
                if container.resources and container.resources.limits:  # Check if resources and limits are not None
                    memory_usage = container.resources.limits.get('memory', '0Gi')
                    if memory_usage:  # Ensure memory_usage is not empty
                        try:
                            if memory_usage.endswith('Mi'):
                                total_memory_usage += int(memory_usage[:-2]) / 1024  # In GiB
                            elif memory_usage.endswith('Gi'):
                                total_memory_usage += int(memory_usage[:-2])  # In GiB
                        except ValueError:
                            print(f"Warning: Invalid Memory usage value '{memory_usage}' for pod '{pod.metadata.name}'")

    return total_memory_usage  # Return in GiB


def get_node_utilization(node):
    cpu_allocatable_str = node.status.allocatable['cpu']
    cpu_allocatable = int(cpu_allocatable_str.replace('m', '')) / 1000 if 'm' in cpu_allocatable_str else int(
        cpu_allocatable_str)
    memory_allocatable = int(node.status.allocatable['memory'].replace('Ki', '')) / (1024 * 1024)

    cpu_capacity_str = node.status.capacity['cpu']
    cpu_capacity = int(cpu_capacity_str.replace('m', '')) / 1000 if 'm' in cpu_capacity_str else int(cpu_capacity_str)
    memory_capacity = round(int(node.status.capacity['memory'].replace('Ki', '')) / (1024 * 1024))  # Округлення

    # Get actual usage
    used_cpu = get_pod_cpu_usage(node)  # Usage by pods
    used_memory = get_pod_memory_usage(node)  # Memory usage by pods

    cpu_utilization = (used_cpu / cpu_capacity) * 100 if cpu_capacity > 0 else 0
    memory_utilization = (used_memory / memory_capacity) * 100 if memory_capacity > 0 else 0
    return cpu_utilization, memory_utilization, cpu_capacity, memory_capacity, used_cpu, used_memory


def display_progress_bar(value, max_value):
    bar_length = 20  # Length of the progress bar
    filled_length = int(bar_length * (value / max_value))
    bar = '█' * filled_length + ' ' * (bar_length - filled_length)

    color = Fore.RED if value < 30 else Fore.YELLOW if value < 80 else Fore.GREEN
    return f"{color}[{bar}] {value:.2f}/{max_value:.2f}{Fore.RESET}"


def analyze_nodes():
    while True:
        nodes = get_nodes()  # Refresh the node list on each cycle
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
                instance_type, price, instance_status = get_instance_details(instance_id)
                cpu_utilization, memory_utilization, cpu_capacity, memory_capacity, used_cpu, used_memory = get_node_utilization(
                    node)
                requests_cpu, requests_memory = get_pod_resource_requests(node)

                node_data.append({
                    "name": node.metadata.name,
                    "instance_type": instance_type,
                    "instance_status": instance_status,  # Add instance status (Spot/On-Demand)
                    "price": price,
                    "cpu_capacity": cpu_capacity,
                    "memory_capacity": memory_capacity,
                    "cpu_utilization": cpu_utilization,
                    "memory_utilization": memory_utilization,
                    "requests_cpu": requests_cpu,
                    "requests_memory": requests_memory,
                    "used_cpu": used_cpu,
                    "used_memory": used_memory
                })

                total_cpu_utilization += cpu_utilization
                total_memory_utilization += memory_utilization
                total_cpu_capacity += cpu_capacity
                total_memory_capacity += memory_capacity
                total_cost += price

                node_count += 1

        # Calculate averages
        avg_cpu_utilization = total_cpu_utilization / node_count if node_count > 0 else 0
        avg_memory_utilization = total_memory_utilization / node_count if node_count > 0 else 0

        # Clear console output
        print("\033c", end="")  # Clear console on Unix systems

        # Print headers
        print(
            f"{'Node Name':<30} | {'Instance Type':<20} | {'Instance Status':<15} | {'Price (USD)':<15} | {'CPU Utilization (%)':<25} | {'Memory Utilization (%)':<25} | {'CPU Capacity (vCPUs)':<20} | {'Memory Capacity (GiB)':<20} | {'Requests CPU (vCPUs)':<20} | {'Requests Memory (GiB)':<20} | {'Used CPU (vCPUs)':<20} | {'Used Memory (GiB)':<20}")
        print("-" * 175)

        # Print node data
        for data in node_data:
            cpu_progress_bar = display_progress_bar(data['cpu_utilization'], 100)
            memory_progress_bar = display_progress_bar(data['memory_utilization'], 100)

            print(
                f"{data['name']:<30} | {data['instance_type']:<20} | {data['instance_status']:<15} | {data['price']:<15.2f} | {cpu_progress_bar:<25} | {memory_progress_bar:<25} | {data['cpu_capacity']:<20.2f} | {round(data['memory_capacity']):<20} | {data['requests_cpu']:<20.2f} | {data['requests_memory']:<20.2f} | {data['used_cpu']:<20.2f} | {data['used_memory']:<20.2f}")

        print("-" * 175)
        print(f"Total CPU Utilization: {avg_cpu_utilization:.2f}%")
        print(f"Total Memory Utilization: {avg_memory_utilization:.2f}%")
        print(f"Total CPU Capacity: {total_cpu_capacity:.2f} vCPUs")
        print(f"Total Memory Capacity: {total_memory_capacity:.2f} GiB")
        print(f"Total Cost: {total_cost:.2f} USD")

        time.sleep(60)  # Wait for 60 seconds before refreshing


if __name__ == "__main__":
    analyze_nodes()
