#!/bin/bash
# Script to run all tests with coverage reporting

# Set up environment
echo "Setting up test environment..."
export PYTHONPATH=$(pwd)

# Check if we have the required packages
if ! command -v pytest &> /dev/null; then
    echo "pytest not found, installing required packages..."
    pip install pytest pytest-asyncio pytest-cov httpx
fi


# Run tests with coverage
echo "Running tests with coverage..."
python -m pytest src/tests/ -v --cov=src --cov-report=term --cov-report=html

# Store the exit status
TEST_STATUS=$?

# Print coverage report summary
echo ""
echo "Coverage summary:"
python -m coverage report

# Instructions for viewing the HTML report
echo ""
echo "For detailed coverage report, open htmlcov/index.html in your browser"

# Return the test status
exit $TEST_STATUS 
