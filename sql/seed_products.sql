insert into products (id, name, category, price) values
 (1,'Cotton T-shirt','Clothing',299),(2,'Denim jacket','Clothing',1499),(3,'Running shoes','Footwear',1999),
 (4,'Canvas sneakers','Footwear',999),(5,'Backpack','Bags',799),(6,'Water bottle','Home',349),
 (7,'Desk lamp','Home',649),(8,'Notebook pack','Stationery',199),(9,'Gel pens','Stationery',149),
 (10,'Earphones','Electronics',499),(11,'Power bank','Electronics',1199),(12,'Phone stand','Electronics',249),
 (13,'Coffee mug','Home',199),(14,'Hoodie','Clothing',1299),(15,'Cap','Clothing',249),
 (16,'Sunglasses','Accessories',599),(17,'Wrist watch','Accessories',1799),(18,'Yoga mat','Fitness',699)
on conflict (id) do nothing;
