"""Pure, deterministic simulation. Time is minutes after midnight, Pacific time.

Each driver can perform one direct delivery per donation. Quantities measure
boxed meals, not physical vehicle volume. These are classroom assumptions.
"""

from copy import deepcopy
from math import asin, cos, radians, sin, sqrt

NEIGHBORHOODS = {
    "Mission": (37.7599, -122.4148),
    "SoMa": (37.7785, -122.4056),
    "Tenderloin": (37.7847, -122.4145),
    "Chinatown": (37.7941, -122.4078),
    "Bayview": (37.7293, -122.3920),
}


def travel_minutes(origin: str, destination: str) -> int:
    """Synthetic drive estimate: straight-line distance, 15km/h, 5min overhead."""
    a, b = NEIGHBORHOODS[origin], NEIGHBORHOODS[destination]
    lat1, lat2 = radians(a[0]), radians(b[0])
    distance = 6371 * 2 * asin(sqrt(
        sin((lat2 - lat1) / 2) ** 2
        + cos(lat1) * cos(lat2) * sin(radians(b[1] - a[1]) / 2) ** 2
    ))
    return max(5, round(distance / 15 * 60 + 5))


def make_world() -> dict:
    restaurants = [
        {"id": f"r{i+1}", "name": f"Demo {n} Kitchen {i+1}", "neighborhood": n}
        for i, n in enumerate(list(NEIGHBORHOODS) * 2)
    ]
    shelters = [
        {"id": f"s{i+1}", "name": f"Demo {n} Shelter {i+1}",
         "neighborhood": n, "capacity": capacity,
         "opens": 14 * 60, "closes": 18 * 60,
         "accepts": ["vegetarian", "vegan", "mixed"], "confirmed": True}
        for i, (n, capacity) in enumerate([
            ("Mission", 20), ("SoMa", 40), ("Tenderloin", 50),
            ("Chinatown", 25), ("Bayview", 40), ("Mission", 30),
        ])
    ]
    drivers = [
        {"id": f"d{i+1}", "name": f"Demo Driver {i+1}", "neighborhood": n,
         "capacity": 40, "starts": 14 * 60, "ends": 18 * 60,
         "available": True, "confirmed": True}
        for i, n in enumerate(NEIGHBORHOODS)
    ]
    return {"now": 14 * 60 + 45, "timezone": "America/Los_Angeles",
            "restaurants": restaurants, "shelters": shelters, "drivers": drivers,
            "reservations": [], "events": []}


def make_donation() -> dict:
    return {"id": "donation-1", "restaurant_id": "r1", "quantity": 60,
            "food_type": "vegetarian", "pickup_start": 15 * 60,
            "pickup_end": 16 * 60, "deliver_by": 17 * 60}


def by_id(items: list[dict], item_id: str) -> dict:
    return next(item for item in items if item["id"] == item_id)


def validate_plan(world: dict, donation: dict, plan: dict) -> list[str]:
    """Validate the entire plan against current state, including existing bookings."""
    errors = []
    required = ["quantity", "food_type", "pickup_start", "pickup_end", "deliver_by", "restaurant_id"]
    if any(donation.get(k) is None for k in required):
        return ["Donation details are incomplete; ask for clarification."]
    legs = plan.get("legs")
    if not isinstance(legs, list) or not legs:
        return ["Plan must contain at least one delivery leg."]
    try:
        restaurant = by_id(world["restaurants"], donation["restaurant_id"])
    except StopIteration:
        return ["Unknown restaurant."]
    used_drivers, allocated, total = set(), {}, 0
    for index, leg in enumerate(legs):
        label = f"Leg {index+1}"
        if not isinstance(leg, dict):
            errors.append(f"{label}: expected an object.")
            continue
        try:
            driver = by_id(world["drivers"], leg.get("driver_id"))
            shelter = by_id(world["shelters"], leg.get("shelter_id"))
        except StopIteration:
            errors.append(f"{label}: unknown driver or shelter.")
            continue
        quantity, pickup, delivery = (leg.get(k) for k in ("quantity", "pickup_at", "deliver_at"))
        if any(type(v) is not int for v in (quantity, pickup, delivery)) or quantity <= 0:
            errors.append(f"{label}: quantity and times must be integers, quantity positive.")
            continue
        total += quantity
        allocated[shelter["id"]] = allocated.get(shelter["id"], 0) + quantity
        if driver["id"] in used_drivers:
            errors.append(f"{label}: driver is assigned more than one trip.")
        used_drivers.add(driver["id"])
        if not driver["available"] or any(r["driver_id"] == driver["id"] for r in world["reservations"]):
            errors.append(f"{label}: driver is unavailable or already booked.")
        if quantity > driver["capacity"]:
            errors.append(f"{label}: driver capacity exceeded.")
        if donation["food_type"] not in shelter["accepts"]:
            errors.append(f"{label}: incompatible food type.")
        earliest = max(world["now"], driver["starts"]) + travel_minutes(driver["neighborhood"], restaurant["neighborhood"])
        if pickup < max(earliest, donation["pickup_start"]) or pickup > donation["pickup_end"]:
            errors.append(f"{label}: pickup window or driver travel violated.")
        if delivery < pickup + 5 + travel_minutes(restaurant["neighborhood"], shelter["neighborhood"]):
            errors.append(f"{label}: delivery allows insufficient travel/loading time.")
        if delivery < shelter["opens"] or delivery > min(shelter["closes"], driver["ends"], donation["deliver_by"]):
            errors.append(f"{label}: receiving, driver, or delivery deadline violated.")
    already_reserved = sum(r["quantity"] for r in world["reservations"] if r["donation_id"] == donation["id"])
    if total + already_reserved > donation["quantity"]:
        errors.append("Donation quantity exceeded, including existing reservations.")
    for shelter_id, quantity in allocated.items():
        booked = sum(r["quantity"] for r in world["reservations"] if r["shelter_id"] == shelter_id)
        if booked + quantity > by_id(world["shelters"], shelter_id)["capacity"]:
            errors.append(f"Shelter {shelter_id}: total receiving capacity exceeded.")
    return errors


def confirmations_complete(world: dict, plan: dict) -> bool:
    return all(by_id(world["shelters"], leg["shelter_id"])["confirmed"]
               and by_id(world["drivers"], leg["driver_id"])["confirmed"]
               for leg in plan["legs"])


def reserve(world: dict, donation: dict, plan: dict) -> dict:
    errors = validate_plan(world, donation, plan)
    if errors:
        raise ValueError("; ".join(errors))
    if not confirmations_complete(world, plan):
        raise ValueError("Recipient and driver confirmations are required.")
    updated = deepcopy(world)
    updated["reservations"].extend(
        dict(leg, donation_id=donation["id"]) for leg in plan["legs"]
    )
    return updated


def cancel_driver(world: dict, donation: dict, driver_id: str) -> dict:
    """Cancel a pre-pickup plan and release all its reservations for replanning."""
    updated = deepcopy(world)
    by_id(updated["drivers"], driver_id)["available"] = False
    updated["reservations"] = [r for r in updated["reservations"] if r["donation_id"] != donation["id"]]
    updated["events"].append({"type": "driver_cancelled", "driver_id": driver_id,
                              "at": updated["now"]})
    return updated
