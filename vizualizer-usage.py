import time
import json
import boto3
from kubernetes import client, config
from colorama import Fore, Style

aws_region = 'eu-west-1'
pricing_region = 'us-east-1'

ec2_client = boto3.client('ec2', region_name=aws_region)

# Завантаження конфігурації Kubernetes для конкретного контексту
config.load_kube_config(context="arn:aws:eks:eu-west-1:294949574448:cluster/dev-1-30")
v1 = client.CoreV1Api()

# Отримуємо всі ноди
nodes = v1.list_node().items

# Функція для отримання Instance ID з анотацій ноди
def get_instance_id(node):
    annotations = node.metadata.annotations
    if 'node.kubernetes.io/instance-id' in annotations:
        return annotations['node.kubernetes.io/instance-id']
    else:
        return get_instance_id_by_internal_ip(node)

# Функція для отримання Instance ID за InternalIP
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

# Функція для отримання деталей інстансу EC2
def get_instance_details(instance_id):
    instance_details = ec2_client.describe_instances(InstanceIds=[instance_id])
    instance_type = instance_details['Reservations'][0]['Instances'][0]['InstanceType']
    price = get_instance_price(instance_type)
    return instance_type, price

# Функція для отримання ціни інстансу
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

    # Перевіряємо наявність результатів
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

# Функція для отримання утилізації CPU та пам'яті
def get_node_utilization(node):
    # Отримуємо allocatable CPU та пам'ять
    cpu_allocatable_str = node.status.allocatable['cpu']
    cpu_allocatable = int(cpu_allocatable_str.replace('m', '')) / 1000 if 'm' in cpu_allocatable_str else int(cpu_allocatable_str)

    memory_allocatable_str = node.status.allocatable['memory']
    memory_allocatable = int(memory_allocatable_str.replace('Ki', '')) / (1024 * 1024)

    # Отримуємо capacity CPU та пам'ять
    cpu_capacity_str = node.status.capacity['cpu']
    cpu_capacity = int(cpu_capacity_str.replace('m', '')) / 1000 if 'm' in cpu_capacity_str else int(cpu_capacity_str)

    memory_capacity_str = node.status.capacity['memory']
    memory_capacity = int(memory_capacity_str.replace('Ki', '')) / (1024 * 1024)

    # Отримуємо зайняті ресурси
    used_cpu = get_pod_cpu_usage(node)  # Функція для обчислення зайнятих CPU
    used_memory = get_pod_memory_usage(node)  # Функція для обчислення зайнятої пам'яті

    # Обчислюємо утилізацію
    cpu_utilization = (used_cpu / cpu_capacity) * 100 if cpu_capacity > 0 else 0
    memory_utilization = (used_memory / memory_capacity) * 100 if memory_capacity > 0 else 0

    return cpu_utilization, memory_utilization, cpu_capacity, memory_capacity, cpu_allocatable, memory_allocatable, used_cpu, used_memory

# Функція для отримання зайнятості CPU (приклад)
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

# Функція для отримання зайнятості пам'яті (приклад)
def get_pod_memory_usage(node):
    pods = v1.list_pod_for_all_namespaces(field_selector=f'spec.nodeName={node.metadata.name}').items
    total_memory_usage = 0

    for pod in pods:
        if pod.spec.containers:
            for container in pod.spec.containers:
                resources = container.resources
                if resources.requests and 'memory' in resources.requests:
                    memory_request = resources.requests['memory']
                    # Перетворюємо в GiB
                    if memory_request.endswith('Mi'):
                        total_memory_usage += int(memory_request[:-2]) / 1024  # В GiB
                    elif memory_request.endswith('Gi'):
                        total_memory_usage += int(memory_request[:-2])  # В GiB

    return total_memory_usage  # Повертаємо в GiB

# Функція для виведення прогрес-бару у стилі htop
def display_htop_style(cpu_utilization, memory_utilization):
    # Визначення кольорів для прогрес-барів
    cpu_color = Fore.RED if cpu_utilization < 30 else Fore.YELLOW if cpu_utilization < 90 else Fore.GREEN
    memory_color = Fore.RED if memory_utilization < 30 else Fore.YELLOW if memory_utilization < 90 else Fore.GREEN

    # Створення прогрес-барів
    cpu_bar = cpu_color + '█' * int(cpu_utilization // 5) + ' ' * (20 - int(cpu_utilization // 5)) + Fore.RESET
    memory_bar = memory_color + '█' * int(memory_utilization // 5) + ' ' * (20 - int(memory_utilization // 5)) + Fore.RESET

    # Виведення прогрес-барів
    print(f"CPU: [{cpu_bar}] {cpu_utilization:.2f}%")
    print(f"Memory: [{memory_bar}] {memory_utilization:.2f}%")

# Основна функція для аналізу нод
def analyze_nodes():
    while True:
        total_cpu_utilization = 0
        total_memory_utilization = 0
        total_cpu_capacity = 0
        total_memory_capacity = 0
        total_cost = 0.0
        node_count = 0

        print("\n" + "-" * 50)
        print(f"{'Node':<30}{'Instance Type':<20}{'Cost (USD/h)':<15}{'CPU Capacity (vCPUs)':<25}{'Memory Capacity (GiB)':<25}{'CPU Utilization (%)':<20}{'Memory Utilization (%)':<25}")
        print("-" * 50)

        for node in nodes:
            instance_id = get_instance_id(node)
            if instance_id:
                instance_type, price = get_instance_details(instance_id)
                cpu_utilization, memory_utilization, cpu_capacity, memory_capacity, cpu_allocatable, memory_allocatable, used_cpu, used_memory = get_node_utilization(node)

                print(f"{node.metadata.name:<30}{instance_type:<20}${price:.4f}{'/hour':<5}{cpu_capacity:<25.2f}{memory_capacity:<25.2f}{cpu_utilization:<20.2f}{memory_utilization:<25.2f}")

                # Відображаємо прогрес-бари для утилізації CPU та пам'яті
                display_htop_style(cpu_utilization, memory_utilization)

                # Накопичуємо загальні значення
                total_cpu_utilization += cpu_utilization
                total_memory_utilization += memory_utilization
                total_cpu_capacity += cpu_capacity
                total_memory_capacity += memory_capacity
                total_cost += price
                node_count += 1

        if node_count > 0:
            print(f"\nAverage CPU Utilization: {total_cpu_utilization / node_count:.2f}%")
            print(f"Average Memory Utilization: {total_memory_utilization / node_count:.2f}%")
            print(f"Total Cost: ${total_cost:.4f}/hour\n")

        time.sleep(30)  # Затримка між ітераціями

# Запускаємо аналіз нод
analyze_nodes()
