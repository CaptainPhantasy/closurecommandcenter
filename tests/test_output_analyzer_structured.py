from output_analyzer import analyze_structured


def test_structured_extracts_python_traceback():
    text = 'Traceback (most recent call last):\n  File "auth.py", line 42, in login\nTypeError: bad\n$ '
    result = analyze_structured(text, process_alive=True, last_output_time=0, current_time=10)
    assert result["state"] == "error"
    assert result["errors"][0]["file"] == "auth.py"
    assert result["errors"][0]["line"] == 42
    assert "TypeError" in result["errors"][0]["message"]


def test_structured_extracts_pytest_counts():
    result = analyze_structured("7 passed, 1 failed in 3.2s\n$ ", True, 0, 10)
    assert result["tests"] == {"passed": 7, "failed": 1}


def test_structured_ready_has_no_errors():
    result = analyze_structured("$ ", True, 0, 10)
    assert result["state"] == "ready"
    assert result["errors"] == []

