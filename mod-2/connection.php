<?php
 $host = "localhost";
 $user = "root";                     //Your Cloud 9 username
 $pass = "";                                  //Remember, there is NO password by default!
 $db = "userform";                                  //Your database name you want to connect to
 //$port = 3306;
 $con = mysqli_connect($host, $user, $pass, $db)or die(mysql_error());
// $con = mysqli_connect('localhost', 'root', '', 'userform');
?>


// // Check connection
// if (!$con) {
//     die("Connection failed: " . mysqli_connect_error());
// }
// echo "Connected successfully!";
// ?>

// if (!$con) {
//     die("Database connection failed: " . mysqli_connect_error());
// }

// // Connect to Redis
// $redis = new Redis();
// $redis->connect('127.0.0.1', 6379); 
// // Change host/port if needed

// if ($redis->ping()) {
//     echo "Connected to Redis!";
// } else {
//     echo "Redis connection failed!";
// }

// // Example: Store and Retrieve Data from Redis
// $redis->set("user:1", "John Doe");
// echo "Stored in Redis: " . $redis->get("user:1");

// ?>
