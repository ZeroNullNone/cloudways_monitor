"""DigitalOcean metric contracts confirmed by the owner on 2026-10-06.

The API still controls which targets exist. These definitions supply units when
the documented graph payload omits them; unknown targets remain unconfirmed.
"""

TARGET_DEFINITIONS = {
    "Idle CPU": {
        "unit": "%",
        "description": "Free CPU capacity. Higher means less busy. Consistently below 10–20% indicates CPU pressure.",
    },
    "Free Disk": {
        "unit": "GB",
        "description": "Free space on the data disk. Below 2 GB indicates disk pressure.",
    },
    "Reads per second": {
        "unit": "ops/s",
        "description": "Disk read operations per second. Sustained values above 1,000 may affect application performance.",
    },
    "Writes per second": {
        "unit": "ops/s",
        "description": "Disk write operations per second. Sustained values above 1,000 may affect application performance.",
    },
    "Free memory": {
        "unit": "B",
        "description": "Unused memory. Source bytes are converted to MB for display. Sustained values below 100 MB indicate memory pressure.",
    },
    "Incoming network traffic": {
        "unit": "Mbps",
        "description": "Incoming network traffic, in megabits per second.",
    },
    "Outgoing network traffic": {
        "unit": "Mbps",
        "description": "Outgoing network traffic, in megabits per second.",
    },
    "Monthly Bandwidth": {
        "unit": "GB",
        "description": "Bandwidth used this month. The counter resets at the start of each month; it is not a transfer rate.",
    },
    "Memcached Fill Ratio": {
        "unit": "%",
        "description": "Used Memcached capacity. Sustained values above 90% may need more cache space.",
    },
    "Memcached Hit Rate": {
        "unit": None,
        "description": "Memcached cache effectiveness. Percentage versus hit-count units are awaiting confirmation; zero cannot establish the scale.",
    },
    "Varnish Hit Rate": {
        "unit": "%",
        "description": "Varnish cache hit percentage. Higher is better; the provided guidance recommends above 80%.",
    },
    "Varnish Nuked": {
        "unit": "items",
        "description": "Items evicted from Varnish because of insufficient space. Repeated nonzero values may need more cache space; chart samples are not summed as event totals.",
    },
    "Auto-healing Restarts": {
        "unit": "restarts",
        "description": "Service restarts after a service is killed, often due to low memory. Frequent restarts warrant investigation; chart samples are not summed as event totals.",
    },
    "MySQL Connections": {
        "unit": "connections",
        "description": "Established MySQL connections. Sustained values above 125 warrant reviewing the configured connection limit.",
    },
}
