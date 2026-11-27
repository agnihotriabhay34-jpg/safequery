"""Creates data/shop.db, a small e-commerce database for trying SafeQuery.  python scripts/make_sample_db.py"""
import os
import random
import sqlite3

os.makedirs("data", exist_ok=True)
path = "data/shop.db"
if os.path.exists(path):
    os.remove(path)

random.seed(7)
conn = sqlite3.connect(path)
conn.executescript("""
CREATE TABLE categories (category_id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE suppliers  (supplier_id INTEGER PRIMARY KEY, name TEXT, country TEXT);
CREATE TABLE products   (product_id INTEGER PRIMARY KEY, name TEXT, price REAL,
                         category_id INTEGER REFERENCES categories(category_id),
                         supplier_id INTEGER REFERENCES suppliers(supplier_id));
CREATE TABLE customers  (customer_id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT, city TEXT, country TEXT);
CREATE TABLE orders     (order_id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(customer_id),
                         order_date TEXT, status TEXT);
CREATE TABLE order_items(item_id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(order_id),
                         product_id INTEGER REFERENCES products(product_id), quantity INTEGER, unit_price REAL);
""")
categories = ["Electronics", "Books", "Clothing", "Home", "Sports"]
conn.executemany("INSERT INTO categories VALUES (?, ?)", list(enumerate(categories, 1)))
conn.executemany("INSERT INTO suppliers VALUES (?, ?, ?)",
                 [(1, "Acme Traders", "India"), (2, "Globex", "USA"), (3, "Initech", "Germany")])
for i in range(1, 41):
    conn.execute("INSERT INTO products VALUES (?, ?, ?, ?, ?)",
                 (i, f"{random.choice(categories)} item {i}", round(random.uniform(99, 4999), 2),
                  random.randint(1, 5), random.randint(1, 3)))
first = ["Aarav", "Diya", "Kabir", "Meera", "Rohan", "Sana", "Vikram", "Anya", "Ishaan", "Priya"]
last = ["Sharma", "Verma", "Gupta", "Singh", "Mishra", "Agarwal"]
cities = ["Kanpur", "Lucknow", "Delhi", "Mumbai", "Bengaluru"]
for i in range(1, 61):
    conn.execute("INSERT INTO customers VALUES (?, ?, ?, ?, 'India')",
                 (i, random.choice(first), random.choice(last), random.choice(cities)))
for i in range(1, 401):
    conn.execute("INSERT INTO orders VALUES (?, ?, ?, ?)",
                 (i, random.randint(1, 60), f"2026-{random.randint(1, 11):02d}-{random.randint(1, 28):02d}",
                  random.choice(["delivered", "shipped", "pending", "cancelled"])))
item = 1
for order_id in range(1, 401):
    for _ in range(random.randint(1, 4)):
        pid = random.randint(1, 40)
        price = conn.execute("SELECT price FROM products WHERE product_id = ?", (pid,)).fetchone()[0]
        conn.execute("INSERT INTO order_items VALUES (?, ?, ?, ?, ?)", (item, order_id, pid, random.randint(1, 3), price))
        item += 1
conn.commit()
conn.close()
print(f"Created {path}")
