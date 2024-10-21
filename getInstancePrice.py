import boto3
import json

# Регион для запитів до Pricing API
pricing_region_name = 'us-east-1'  # Цей регіон використовується для Pricing API

# Ініціалізація клієнта Pricing
pricing_client = boto3.client('pricing', region_name=pricing_region_name)

# Список типів інстансів для перевірки
instance_types = ['c5a.large', 't4g.xlarge']

def get_instance_price(instance_type):
    # Виконуємо запит до Pricing API
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
                        return price[price_key]['pricePerUnit']['USD']
    return "Unknown"

# Отримуємо та виводимо ціни для всіх типів інстансів
for instance_type in instance_types:
    price = get_instance_price(instance_type)
    print(f"Instance Type: {instance_type} - Price: ${price}/hour")
