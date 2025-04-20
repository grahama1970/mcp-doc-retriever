# Subqueries in AQL

AQL subqueries are a powerful feature that allows you to performou complex data retrieval and manipulation operations.

## Basic Subqueries

A basic subquery is an AQL query embedded within another AQL query. The inner query (subquery) is executed for each document in the outer query.

### Example 1: Simple Friends Subquery

This example retrieves the friends of a user. It uses the `FILTER friend.userId == user._id` condition to match friends.

```aql
FOR user IN users
  LET friends = (
    FOR friend IN friends
    FILTER friend.userId == user._id
    RETURN friend
  )
  RETURN {
    user: user,
    friends: friends
  }
```

### Example 2: Orders Subquery

This example retrieves the orders placed by a user.  It's a correlated subquery.

```aql
FOR user IN users
  LET orders = (
    FOR order IN orders
    FILTER order.userId == user._id
    RETURN order
  )
  RETURN {
    user: user,
    orders: orders
  }
```

### Example 3: Items in Order Subquery

This example retrieves the items included in each order of a user

```aql
FOR user IN users
  LET orders = (
    FOR order IN orders
    FILTER order.userId == user._id
    LET items = (
      FOR item IN order.items
        RETURN item
    )
    RETURN {order: order, items: items}
  )
  RETURN {
    user: user,
    orders: orders
  }
```

### Subquery Results

The result of a subquery is always an array. This array can then be processed further in the outer query. For example, you can use `LENGTH()` to determine the number of results.

## Correlated Subqueries

Correlated subqueries refer to variables from the outer query. In the following example, the `user` variable from the outer loop is used in the inner loop.

```aql
FOR user IN users
  LET orders = (
    FOR order IN orders
    FILTER order.userId == user._id
    RETURN order
  )
  RETURN {
    user: user,
    orders: orders
  }
```

## Non-Correlated Subqueries

Non-correlated subqueries do not depend on variables from the outer query. They are essentially independent queries that are executed once and their result is used in the outer query.  They can be useful, but also less efficient.

```aql
LET activeUsers = (
  FOR user IN users
  FILTER user.status == "active"
  RETURN user
)
FOR user IN activeUsers
  RETURN user
```

## Using Subqueries in FILTER conditions

Subqueries can be used within `FILTER` conditions to restrict the result set based on complex criteria. The following conditions apply:

*   The subquery must return a boolean value.
*   The subquery should be efficient.
*   Consider using `EXISTS()` for existence checks.

```aql
FOR user IN users
  FILTER LENGTH(
    FOR friend IN friends
    FILTER friend.userId == user._id
    RETURN 1
  ) > 5
  RETURN user
```

# Advanced Subquery Usage

This section covers more advanced techniques for using subqueries in AQL.

## Executing AQL Subqueries

This section provides examples of how to execute AQL subqueries in different environments.

### Executing AQL in Python (python-arango)

Here's how to execute an AQL query using the `python-arango` driver:

```python
from arango import ArangoClient

def execute_aql_query(db_name, aql_query, bind_vars={}):
    """
    Executes an AQL query against an ArangoDB database using the python-arango driver.

    Args:
        db_name (str): The name of the ArangoDB database.
        aql_query (str): The AQL query to execute.
        bind_vars (dict, optional): Bind variables for the query. Defaults to {}.

    Returns:
        list: The result of the query as a list of dictionaries.
    """
    # Initialize the ArangoDB client.
    client = ArangoClient(hosts="http://localhost:8529")

    # Connect to the database.
    db = client.db(db_name, username="root", password="your_password")

    # Execute the query.
    cursor = db.aql.execute(aql_query, bind_vars=bind_vars)

    # Return the results.
    return list(cursor)

# Example usage
if __name__ == "__main__":
    query = """
    FOR user IN users
      LET friends = (
        FOR friend IN friends
        FILTER friend.userId == user._id
        RETURN friend
      )
      RETURN {
        user: user,
        friends: friends
      }
    """
    results = execute_aql_query("your_db_name", query)
    print(results)
```

### Executing AQL in JavaScript (ArangoDB Shell)

Here's how to execute the same AQL query in the ArangoDB shell (JavaScript):

```javascript
// Connect to the database
const db = require("@arangodb").db;

// AQL query
let query = `
  FOR user IN users
    LET friends = (
      FOR friend IN friends
      FILTER friend.userId == user._id
      RETURN friend
    )
    RETURN {
      user: user,
      friends: friends
    }
`;

// Execute the query
let cursor = db._query(query);

// Print the results
while (cursor.hasNext()) {
  console.log(cursor.next());
}
```
