"""Sample payloads shaped like the Best Buy Open Box API responses."""

OPEN_BOX_RESPONSE = {
    "metadata": {"resultSet": {"count": 1}},
    "results": [
        {
            "sku": 6618354,
            "names": {"title": 'LG - 97" Class G5 Series OLED evo 4K Smart TV'},
            "prices": {"regular": 24999.99},
            "url": "https://www.bestbuy.com/site/p/6618354.p",
            "offers": [
                {
                    "condition": "excellent",
                    "prices": {"current": 2000.0, "regular": 24999.99},
                    "links": {"web": "https://www.bestbuy.com/site/p/6618354.p?condition=excellent"},
                    "inventory": {"onlineAvailability": True},
                },
                {
                    "condition": "fair",
                    "prices": {"current": 1800.0, "regular": 24999.99},
                    "inventory": {"onlineAvailability": False},
                },
                {
                    "condition": "satisfactory",
                    "prices": {},
                },
            ],
        }
    ],
}

PRODUCT_SEARCH_RESPONSE = {
    "products": [{"sku": 6618354, "name": "LG G5", "regularPrice": 24999.99},
                 {"sku": 6577100, "name": "LG G4", "regularPrice": 4999.99}]
}

STORES_RESPONSE = {
    "stores": [
        {"name": "Schaumburg", "city": "Schaumburg", "distance": 7.4},
        {"name": "Niles", "city": "Niles", "distance": 12.1},
    ]
}
