import time
import json
import boto3
from kubernetes import client, config
from colorama import Fore, Style

aws_region = 'eu-west-1'
pricing_region = 'us-east-1'

ec2_client = boto3.client('ec2', region_name=aws_region)

config.load_kube_config(context="arn:aws:eks:eu-west-1:294949574448:cluster/dev-1-30")
v1 = client.CoreV1Api()

nodes = v1.list_node().items


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
    # Виконуємо запит до Pricing API
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
            price_item_dict = json.loads(price_item)  # Перетворюємо рядок JSON в словник
            if 'terms' in price_item_dict:
                price_dimensions = price_item_dict['terms']['OnDemand']
                for key in price_dimensions:
                    price = price_dimensions[key]['priceDimensions']
                    for price_key in price:
                        return float(price[price_key]['pricePerUnit']['USD'])  # Повертаємо ціну як float
    return 0.0  # Повертаємо 0.0, якщо не вдалося отримати ціну


def get_node_utilization(node):
    cpu_allocatable_str = node.status.allocatable['cpu']
    cpu_allocatable = int(cpu_allocatable_str.replace('m', '')) / 1000 if 'm' in cpu_allocatable_str else int(
        cpu_allocatable_str)
    memory_allocatable = int(node.status.allocatable['memory'].replace('Ki', '')) / (1024 * 1024)

    cpu_capacity_str = node.status.capacity['cpu']
    cpu_capacity = int(cpu_capacity_str.replace('m', '')) / 1000 if 'm' in cpu_capacity_str else int(cpu_capacity_str)
    memory_capacity = int(node.status.capacity['memory'].replace('Ki', '')) / (1024 * 1024)

    cpu_utilization = (cpu_allocatable / cpu_capacity) * 100
    memory_utilization = (memory_allocatable / memory_capacity) * 100
    return cpu_utilization, memory_utilization, cpu_capacity, memory_capacity


def display_htop_style(cpu_utilization, memory_utilization):
    cpu_color = Fore.GREEN if cpu_utilization >= 90 else Fore.YELLOW if cpu_utilization >= 30 else Fore.RED
    memory_color = Fore.GREEN if memory_utilization >= 90 else Fore.YELLOW if memory_utilization >= 30 else Fore.RED

    cpu_bar = cpu_color + '█' * int(cpu_utilization // 5) + ' ' * (20 - int(cpu_utilization // 5)) + Fore.RESET
    memory_bar = memory_color + '█' * int(memory_utilization // 5) + ' ' * (
            20 - int(memory_utilization // 5)) + Fore.RESET

    print(f"CPU: [{cpu_bar}] {cpu_utilization:.2f}%")
    print(f"Memory: [{memory_bar}] {memory_utilization:.2f}%")


def analyze_nodes():
    while True:
        total_cpu_utilization = 0
        total_memory_utilization = 0
        total_cpu_capacity = 0
        total_memory_capacity = 0
        total_cost = 0.0
        node_count = 0

        for node in nodes:
            instance_id = get_instance_id(node)
            if instance_id:
                instance_type, price = get_instance_details(instance_id)
                cpu_utilization, memory_utilization, cpu_capacity, memory_capacity = get_node_utilization(node)

                print(f"\nInstance ID: {node.metadata.name}")
                print(f"Instance Type: {instance_type}")
                print(f"Node Pricing: ${price:.4f}/hour")
                print(f"CPU Capacity: {cpu_capacity:.2f} vCPUs")
                print(f"Memory Capacity: {memory_capacity:.2f} GiB")

                display_htop_style(cpu_utilization, memory_utilization)

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
