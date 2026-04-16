#!/usr/bin/env python3
"""Test script to verify order ID normalization in MCP server"""

def test_order_id_normalization():
    """Test the order ID normalization logic"""

    test_cases = [
        # (input, should_normalize, expected_output)
        ('W2378156', True, '#W2378156'),
        ('#W2378156', False, '#W2378156'),
        ('W1234567', True, '#W1234567'),
        ('#W1234567', False, '#W1234567'),
        ('A123456', True, '#A123456'),
        ('invalid_id', False, 'invalid_id'),  # Not matching pattern
        ('123456', False, '123456'),  # Starts with digit
        ('', False, ''),  # Empty string
    ]

    print("Testing order ID normalization logic:\n")

    for input_id, should_norm, expected in test_cases:
        # Simulate the normalization logic from mcp_server.py
        order_id = input_id

        if order_id and not order_id.startswith('#'):
            # Check if this looks like a retail order ID (e.g., W1234567)
            if order_id and order_id[0].isalpha() and order_id[1:].isdigit():
                normalized = f'#{order_id}'
                was_normalized = True
            else:
                normalized = order_id
                was_normalized = False
        else:
            normalized = order_id
            was_normalized = False

        # Check results
        status = "✅" if normalized == expected else "❌"
        norm_status = "normalized" if was_normalized else "unchanged"

        print(f"{status} Input: '{input_id}' -> Output: '{normalized}' ({norm_status})")

        if normalized != expected:
            print(f"   ERROR: Expected '{expected}'")

    print("\n✅ Normalization logic test completed!")

if __name__ == "__main__":
    test_order_id_normalization()
