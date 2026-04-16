#!/usr/bin/env python3
"""Test script to verify order_id normalization in trajectory recording"""

import sys


def normalize_order_id(arguments: dict) -> dict:
    """Normalize order_id in arguments to include '#' prefix if needed.

    This is a copy of the function from nexusagent_backend.py for testing.
    """
    if 'order_id' not in arguments:
        return arguments

    order_id = arguments.get('order_id')
    if not isinstance(order_id, str) or not order_id or order_id.startswith('#'):
        return arguments

    # Check if this looks like a retail order ID (e.g., W1234567)
    if order_id[0].isalpha() and order_id[1:].isdigit():
        normalized_args = arguments.copy()
        normalized_args['order_id'] = f'#{order_id}'
        return normalized_args

    return arguments


def test_normalize_order_id():
    """Test the normalize_order_id function used for trajectory recording"""

    test_cases = [
        # (input_args, expected_output_args, description)
        (
            {'order_id': 'W2378156', 'other': 'value'},
            {'order_id': '#W2378156', 'other': 'value'},
            "Should add # prefix to retail order ID"
        ),
        (
            {'order_id': '#W2378156', 'other': 'value'},
            {'order_id': '#W2378156', 'other': 'value'},
            "Should not modify already-correct order ID"
        ),
        (
            {'other': 'value'},
            {'other': 'value'},
            "Should not modify dict without order_id"
        ),
        (
            {'order_id': 'invalid_id', 'other': 'value'},
            {'order_id': 'invalid_id', 'other': 'value'},
            "Should not modify non-matching order_id pattern"
        ),
        (
            {'order_id': '', 'other': 'value'},
            {'order_id': '', 'other': 'value'},
            "Should not modify empty order_id"
        ),
        (
            {'order_id': 'A123456'},
            {'order_id': '#A123456'},
            "Should add # prefix to any letter+digits pattern"
        ),
    ]

    print("Testing normalize_order_id() for trajectory recording:\n")

    all_passed = True
    for input_args, expected, description in test_cases:
        result = normalize_order_id(input_args)
        passed = result == expected

        status = "✅" if passed else "❌"
        print(f"{status} {description}")
        print(f"   Input:    {input_args}")
        print(f"   Expected: {expected}")
        print(f"   Got:      {result}")

        if not passed:
            all_passed = False
            print(f"   ⚠️  FAILED!")
        print()

    if all_passed:
        print("✅ All trajectory normalization tests passed!")
        return 0
    else:
        print("❌ Some tests failed!")
        return 1


if __name__ == "__main__":
    sys.exit(test_normalize_order_id())
