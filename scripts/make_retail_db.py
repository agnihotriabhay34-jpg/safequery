"""Builds the demo database: a fictional Indian retail company (19 tables, ~400k rows).

  python scripts/make_retail_db.py [output path, default data/retail_india.db]

Fixed random seed, so every build produces the same data (the README's example answers stay true).
"""
import sqlite3, random, os, sys, datetime as dt
random.seed(42)
P = sys.argv[1] if len(sys.argv) > 1 else "data/retail_india.db"
os.makedirs(os.path.dirname(P) or ".", exist_ok=True)
if os.path.exists(P): os.remove(P)
c=sqlite3.connect(P)
c.executescript("""
PRAGMA foreign_keys=ON;
CREATE TABLE regions(region_id INTEGER PRIMARY KEY, region_name TEXT NOT NULL);
CREATE TABLE stores(store_id INTEGER PRIMARY KEY, store_name TEXT, city TEXT, state TEXT, region_id INTEGER REFERENCES regions(region_id), opened_on TEXT, store_type TEXT);
CREATE TABLE departments(department_id INTEGER PRIMARY KEY, department_name TEXT);
CREATE TABLE employees(employee_id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT, job_title TEXT, department_id INTEGER REFERENCES departments(department_id), store_id INTEGER REFERENCES stores(store_id), manager_id INTEGER REFERENCES employees(employee_id), hire_date TEXT, monthly_salary REAL);
CREATE TABLE customers(customer_id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT, email TEXT, gender TEXT, birth_year INTEGER, city TEXT, state TEXT, segment TEXT, signup_date TEXT);
CREATE TABLE loyalty_accounts(loyalty_id INTEGER PRIMARY KEY, customer_id INTEGER UNIQUE REFERENCES customers(customer_id), tier TEXT, points_balance INTEGER, joined_on TEXT);
CREATE TABLE categories(category_id INTEGER PRIMARY KEY, category_name TEXT, parent_category_id INTEGER REFERENCES categories(category_id));
CREATE TABLE brands(brand_id INTEGER PRIMARY KEY, brand_name TEXT, country_of_origin TEXT);
CREATE TABLE suppliers(supplier_id INTEGER PRIMARY KEY, supplier_name TEXT, city TEXT, rating REAL);
CREATE TABLE products(product_id INTEGER PRIMARY KEY, product_name TEXT, category_id INTEGER REFERENCES categories(category_id), brand_id INTEGER REFERENCES brands(brand_id), supplier_id INTEGER REFERENCES suppliers(supplier_id), mrp REAL, cost_price REAL, launch_date TEXT, is_active INTEGER);
CREATE TABLE inventory(store_id INTEGER REFERENCES stores(store_id), product_id INTEGER REFERENCES products(product_id), quantity_on_hand INTEGER, reorder_level INTEGER, last_restocked TEXT, PRIMARY KEY(store_id, product_id));
CREATE TABLE promotions(promotion_id INTEGER PRIMARY KEY, promo_code TEXT, description TEXT, discount_percent REAL, start_date TEXT, end_date TEXT);
CREATE TABLE orders(order_id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(customer_id), store_id INTEGER REFERENCES stores(store_id), employee_id INTEGER REFERENCES employees(employee_id), order_date TEXT, channel TEXT, order_status TEXT, promotion_id INTEGER REFERENCES promotions(promotion_id));
CREATE TABLE order_items(order_item_id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(order_id), product_id INTEGER REFERENCES products(product_id), quantity INTEGER, unit_price REAL, discount_amount REAL);
CREATE TABLE payments(payment_id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(order_id), payment_method TEXT, amount REAL, payment_status TEXT, paid_at TEXT);
CREATE TABLE shipments(shipment_id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(order_id), carrier TEXT, shipped_date TEXT, delivered_date TEXT, shipping_cost REAL);
CREATE TABLE returns(return_id INTEGER PRIMARY KEY, order_item_id INTEGER REFERENCES order_items(order_item_id), return_date TEXT, reason TEXT, refund_amount REAL);
CREATE TABLE product_reviews(review_id INTEGER PRIMARY KEY, product_id INTEGER REFERENCES products(product_id), customer_id INTEGER REFERENCES customers(customer_id), rating INTEGER, review_date TEXT, verified_purchase INTEGER);
CREATE TABLE support_tickets(ticket_id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(customer_id), order_id INTEGER REFERENCES orders(order_id), issue_type TEXT, priority TEXT, status TEXT, opened_at TEXT, resolved_at TEXT, assigned_employee_id INTEGER REFERENCES employees(employee_id));
""")
D0=dt.date(2024,1,1)
def d(a=0,b=1060): return (D0+dt.timedelta(days=random.randint(a,b)))
regions=["North","South","East","West","Central"]
c.executemany("insert into regions values(?,?)",list(enumerate(regions,1)))
cities=[("Kanpur","Uttar Pradesh",1),("Lucknow","Uttar Pradesh",1),("Delhi","Delhi",1),("Jaipur","Rajasthan",4),("Chandigarh","Punjab",1),("Mumbai","Maharashtra",4),("Pune","Maharashtra",4),("Ahmedabad","Gujarat",4),("Bengaluru","Karnataka",2),("Chennai","Tamil Nadu",2),("Hyderabad","Telangana",2),("Kochi","Kerala",2),("Kolkata","West Bengal",3),("Patna","Bihar",3),("Bhubaneswar","Odisha",3),("Bhopal","Madhya Pradesh",5),("Indore","Madhya Pradesh",5),("Nagpur","Maharashtra",5),("Varanasi","Uttar Pradesh",1),("Guwahati","Assam",3)]
stores=[]
for i in range(1,31):
    ci=cities[(i-1)%len(cities)]
    stores.append((i,f"{ci[0]} {random.choice(['Central','Mall','Plaza','City Centre','Express'])} #{i}",ci[0],ci[1],ci[2],d(-2000,0).isoformat(),random.choice(["Flagship","Standard","Express"])))
c.executemany("insert into stores values(?,?,?,?,?,?,?)",stores)
depts=["Sales","Operations","Customer Support","Inventory","Finance","HR","Marketing"]
c.executemany("insert into departments values(?,?)",list(enumerate(depts,1)))
F=["Aarav","Vivaan","Aditya","Arjun","Sai","Reyansh","Krishna","Ishaan","Rohan","Kabir","Ananya","Diya","Saanvi","Aadhya","Kiara","Meera","Priya","Riya","Sneha","Pooja","Rahul","Amit","Neha","Kavya","Vikram","Sanjay","Deepika","Nisha","Manish","Alok"]
L=["Sharma","Verma","Gupta","Singh","Mishra","Agarwal","Patel","Reddy","Nair","Iyer","Das","Banerjee","Yadav","Joshi","Kumar","Chauhan","Mehta","Shah","Pillai","Agnihotri"]
emps=[]; eid=1
for s in range(1,31):
    mgr=eid
    emps.append((eid,random.choice(F),random.choice(L),"Store Manager",1,s,None,d(-1500,200).isoformat(),round(random.uniform(60000,110000),0))); eid+=1
    for _ in range(random.randint(6,12)):
        dep=random.randint(1,7)
        title={1:"Sales Associate",2:"Operations Executive",3:"Support Agent",4:"Inventory Clerk",5:"Accountant",6:"HR Executive",7:"Marketing Executive"}[dep]
        emps.append((eid,random.choice(F),random.choice(L),title,dep,s,mgr,d(-1200,900).isoformat(),round(random.uniform(18000,55000),0))); eid+=1
c.executemany("insert into employees values(?,?,?,?,?,?,?,?,?)",emps)
sales_by_store={}
for e in emps:
    if e[4]==1: sales_by_store.setdefault(e[5],[]).append(e[0])
support=[e[0] for e in emps if e[4]==3]
segs=["Consumer","Corporate","Small Business","Student"]
cust=[]
for i in range(1,8001):
    fn,ln=random.choice(F),random.choice(L); ci=random.choice(cities)
    cust.append((i,fn,ln,f"{fn.lower()}.{ln.lower()}{i}@example.com",random.choice(["M","F"]),random.randint(1960,2007),ci[0],ci[1],random.choices(segs,[60,15,15,10])[0],d(-400,1000).isoformat()))
c.executemany("insert into customers values(?,?,?,?,?,?,?,?,?,?)",cust)
la=[];lid=1
for cu in cust:
    if random.random()<0.45:
        la.append((lid,cu[0],random.choices(["Silver","Gold","Platinum"],[60,30,10])[0],random.randint(0,25000),cu[9])); lid+=1
c.executemany("insert into loyalty_accounts values(?,?,?,?,?)",la)
top=["Electronics","Fashion","Home & Kitchen","Grocery","Sports & Fitness","Books","Beauty & Personal Care"]
subs={"Electronics":["Mobiles","Laptops","Headphones","Televisions","Smartwatches"],"Fashion":["Men's Clothing","Women's Clothing","Footwear","Watches"],"Home & Kitchen":["Cookware","Furniture","Appliances","Decor"],"Grocery":["Staples","Snacks","Beverages","Dairy"],"Sports & Fitness":["Cricket","Gym Equipment","Cycling"],"Books":["Fiction","Academic","Self-help"],"Beauty & Personal Care":["Skincare","Haircare","Fragrances"]}
cats=[];cid=1;leaf=[]
for t in top:
    pid=cid; cats.append((cid,t,None)); cid+=1
    for s in subs[t]: cats.append((cid,s,pid)); leaf.append((cid,t,s)); cid+=1
c.executemany("insert into categories values(?,?,?)",cats)
brandnames=[("Samsung","South Korea"),("Apple","USA"),("boAt","India"),("Lenovo","China"),("Sony","Japan"),("Titan","India"),("Fabindia","India"),("Bata","India"),("Prestige","India"),("Godrej","India"),("Tata","India"),("Amul","India"),("Haldiram's","India"),("Decathlon","France"),("SG","India"),("Hero","India"),("Penguin","UK"),("Lakme","India"),("Himalaya","India"),("Nike","USA"),("Puma","Germany"),("Philips","Netherlands"),("LG","South Korea"),("Mamaearth","India"),("Nestle","Switzerland")]
c.executemany("insert into brands values(?,?,?)",[(i,b,o) for i,(b,o) in enumerate(brandnames,1)])
sup=[(i,f"{random.choice(['Shree','Om','Balaji','Krishna','National','Prime','Galaxy'])} {random.choice(['Traders','Distributors','Enterprises','Wholesale'])} {i}",random.choice(cities)[0],round(random.uniform(2.5,5),1)) for i in range(1,41)]
c.executemany("insert into suppliers values(?,?,?,?)",sup)
price_band={"Electronics":(999,149999),"Fashion":(299,9999),"Home & Kitchen":(199,49999),"Grocery":(20,1500),"Sports & Fitness":(199,39999),"Books":(99,1999),"Beauty & Personal Care":(99,4999)}
prods=[]
for i in range(1,1201):
    lc,t,s=random.choice(leaf); lo,hi=price_band[t]
    mrp=round(random.uniform(lo,hi)/10)*10-1
    prods.append((i,f"{random.choice(brandnames)[0]} {s} {random.choice(['Pro','Lite','Max','Classic','Plus','Neo','Prime'])} {i}",lc,random.randint(1,len(brandnames)),random.randint(1,40),mrp,round(mrp*random.uniform(0.45,0.8),2),d(-900,900).isoformat(),1 if random.random()<0.9 else 0))
c.executemany("insert into products values(?,?,?,?,?,?,?,?,?)",prods)
inv=[]
for s in range(1,31):
    for p in random.sample(range(1,1201),500):
        inv.append((s,p,random.randint(0,200),random.randint(5,30),d(900,1060).isoformat()))
c.executemany("insert into inventory values(?,?,?,?,?)",inv)
promos=[(1,"DIWALI20","Diwali festive sale",20,"2024-10-20","2024-11-05"),(2,"NEWYEAR10","New year offer",10,"2025-01-01","2025-01-07"),(3,"HOLI15","Holi celebration",15,"2025-03-10","2025-03-16"),(4,"MONSOON25","Monsoon clearance",25,"2025-07-01","2025-07-15"),(5,"DIWALI25","Diwali festive sale",25,"2025-10-10","2025-10-25"),(6,"REPUBLIC12","Republic Day sale",12,"2026-01-20","2026-01-27"),(7,"SUMMER18","Summer sale",18,"2026-05-01","2026-05-15"),(8,"WELCOME5","First order discount",5,"2024-01-01","2026-12-31")]
c.executemany("insert into promotions values(?,?,?,?,?,?)",promos)
pd={p[0]:p for p in prods}
orders=[];items=[];pays=[];ships=[];rets=[];oi=1;pid_=1;sid=1;rid=1
statuses=["Delivered","Delivered","Delivered","Delivered","Shipped","Processing","Cancelled","Returned"]
for o in range(1,70001):
    cu=random.randint(1,8000); st=random.randint(1,30); od=d(0,1060)
    ch=random.choices(["In-Store","Online","Mobile App"],[45,35,20])[0]
    status=random.choice(statuses)
    promo=None
    for pr in promos:
        if pr[4]<=od.isoformat()<=pr[5] and random.random()<0.5: promo=pr[0]
    emp=random.choice(sales_by_store.get(st,[None])) if ch=="In-Store" else None
    orders.append((o,cu,st,emp,od.isoformat(),ch,status,promo))
    total=0
    for _ in range(random.choices([1,2,3,4,5],[40,30,15,10,5])[0]):
        p=random.randint(1,1200); q=random.choices([1,2,3],[75,18,7])[0]
        up=round(pd[p][5]*random.uniform(0.85,1.0),2)
        disc=round(up*q*(dict((x[0],x[3]) for x in promos)[promo]/100),2) if promo else 0.0
        items.append((oi,o,p,q,up,disc)); total+=up*q-disc
        if status=="Returned" and random.random()<0.7:
            rets.append((rid,oi,(od+dt.timedelta(days=random.randint(5,25))).isoformat(),random.choice(["Damaged product","Wrong item delivered","Size issue","Quality not as expected","Changed mind","Late delivery"]),round(up*q-disc,2))); rid+=1
        oi+=1
    meth=random.choices(["UPI","Credit Card","Debit Card","Cash","Net Banking","Wallet"],[40,18,15,15 if ch=="In-Store" else 2,7,5])[0]
    pstat="Refunded" if status in("Cancelled","Returned") else ("Pending" if status=="Processing" and random.random()<0.5 else "Completed")
    if status=="Cancelled" and random.random()<0.3: pstat="Failed"
    pays.append((pid_,o,meth,round(total,2),pstat,(od.isoformat()+f" {random.randint(9,21):02d}:{random.randint(0,59):02d}:00"))); pid_+=1
    if ch!="In-Store" and status in("Delivered","Shipped","Returned"):
        sd=od+dt.timedelta(days=random.randint(0,3))
        dd=(sd+dt.timedelta(days=random.randint(1,9))).isoformat() if status!="Shipped" else None
        ships.append((sid,o,random.choice(["Delhivery","Blue Dart","Ekart","DTDC","India Post","Shadowfax"]),sd.isoformat(),dd,random.choice([0,0,40,49,79,99]))); sid+=1
c.executemany("insert into orders values(?,?,?,?,?,?,?,?)",orders)
c.executemany("insert into order_items values(?,?,?,?,?,?)",items)
c.executemany("insert into payments values(?,?,?,?,?,?)",pays)
c.executemany("insert into shipments values(?,?,?,?,?,?)",ships)
c.executemany("insert into returns values(?,?,?,?,?)",rets)
revs=[(i,random.randint(1,1200),random.randint(1,8000),random.choices([1,2,3,4,5],[6,8,16,35,35])[0],d(30,1060).isoformat(),1 if random.random()<0.8 else 0) for i in range(1,25001)]
c.executemany("insert into product_reviews values(?,?,?,?,?,?)",revs)
tk=[]
for i in range(1,9001):
    o=random.choice(orders); op=d(0,1060)
    stt=random.choices(["Resolved","Closed","Open","In Progress","Escalated"],[45,25,12,12,6])[0]
    res=(op+dt.timedelta(hours=0,days=random.randint(0,10))).isoformat() if stt in("Resolved","Closed") else None
    tk.append((i,o[1],o[0],random.choice(["Delivery delay","Refund not received","Damaged item","Payment failed","Wrong item","Account issue","Product query"]),random.choices(["Low","Medium","High","Critical"],[30,40,22,8])[0],stt,op.isoformat(),res,random.choice(support)))
c.executemany("insert into support_tickets values(?,?,?,?,?,?,?,?,?)",tk)
c.commit()
for (n,) in c.execute("select name from sqlite_master where type='table' order by name"):
    print(f"{n:18}{c.execute(f'select count(*) from {n}').fetchone()[0]:>8}")
c.execute("VACUUM"); c.close()
print(os.path.getsize(P)//1024,"KB")
