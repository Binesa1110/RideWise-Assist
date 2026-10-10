from access_control import AccessControl, DevelopmentUser, can_access_record


def test_support_can_access_all_records():
    support = DevelopmentUser(2, "support", "support")
    assert AccessControl().authorize(support, "booking", 10) is True


def test_customer_cannot_view_other_customer_data():
    customer = DevelopmentUser(1, "customer", "customer")
    assert AccessControl().authorize(customer, "payment", 999) is False


def test_can_access_record_helper():
    assert can_access_record("customer", "customer", "booking", 1) is True
    assert can_access_record("customer", "customer", "booking", 2) is False
