# Testing Guide

This project uses pytest for testing along with coverage reporting. The tests are organized to ensure the APIs and business logic work correctly.

## Running Tests

To run all the tests with coverage reporting, use the provided script:

```bash
./run_tests.sh
```

This will:
1. Run all tests in the `src/tests/` directory
2. Generate a coverage report in the terminal
3. Create an HTML coverage report in the `htmlcov/` directory

## Test Structure

The tests are organized into several categories:

1. **API Route Tests**:
   - `test_company_routes.py` - Tests for company CRUD operations
   - `test_user_routes.py` - Tests for user management
   - `test_email_routes.py` - Tests for email operations
   - `test_filter_routes.py` - Tests for filter functionality
   - `test_query_routes.py` - Tests for query operations
   - `test_dashboard_routes.py` - Tests for dashboard functionality

2. **Controller Tests**:
   - `test_company_controller.py` - Tests for company business logic

3. **Model Tests**:
   - `test_models.py` - Tests for database models (PortfolioCompany, User)

## Mocking Strategy

The tests use mocking to avoid actual database connections during testing:

- API tests mock the database session dependency using `patch` and `MagicMock`
- Controller tests mock the database session and query results
- Database interaction tests are configured to use an in-memory or test database

## Test Coverage

The test suite provides coverage for:
- All API endpoints
- Business logic in controller classes
- Data models and database interactions
- Error handling and edge cases

Current coverage is approximately 60% across the codebase. Migrations, configuration files, and some utility code are excluded from coverage calculations.

## Common Testing Patterns

Tests follow these common patterns:
1. **Setup**: Prepare test data and mock dependencies
2. **Action**: Make API calls or invoke methods
3. **Assertion**: Verify response structure and values
4. **Verification**: Ensure mocks were called as expected

## Improving Tests

To improve test coverage:

1. Add more tests for database interactions
2. Add tests for utility functions
3. Add more test cases for error handling
4. Implement integration tests that test multiple components together

## Test Configuration

Test configuration is controlled via `pytest.ini` in the project root. This configures:

- Test discovery paths
- Test naming patterns
- Logging settings
- Coverage settings

## Running Specific Tests

To run specific test files or test functions:

```bash
# Run a specific test file
python -m pytest src/tests/test_company_routes.py

# Run a specific test function
python -m pytest src/tests/test_company_routes.py::test_get_company_list

# Run tests with certain names
python -m pytest -k "company"

# Run tests for a specific module
python -m pytest src/tests/test_user_routes.py
``` 