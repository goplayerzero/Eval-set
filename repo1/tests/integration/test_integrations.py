import main

def test_add_integration():         
    result = main.add(2, 3)
    assert result == 5

def test_subtract_integration():
    result = main.subtract(5, 3)
    assert result == 2

def test_multiply_integration():
    result = main.multiply(2, 4)
    assert result == 8

def test_divide_integration():
    result = main.divide(6, 2)
    assert result == 3

def test_divide_by_zero_integration():
    try:
        main.divide(5, 0)
        assert False, "No exception raised"
    except ValueError as e:
        assert str(e) == "Cannot divide by zero"

def test_api_endpoint_integration(sample_data):
    result = main.api_endpoint(sample_data)
    assert "Processed" in result
    assert sample_data in result