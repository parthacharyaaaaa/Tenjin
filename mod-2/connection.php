<?php
// MySQL Connection
$host = "localhost";
$user = "root";                     
$pass = ""; // No password by default
$db = "userform";                                  

$con = mysqli_connect($host, $user, $pass, $db);
if (!$con) {
    die("Database connection failed: " . mysqli_connect_error());
}

// Redis Connection
$redis = new Redis();
try {
    $redis->connect('127.0.0.1', 6379);
    echo "Connected to Redis!";
} catch (Exception $e) {
    die("Redis connection failed: " . $e->getMessage());
}

// Example: Storing and Retrieving Data from Redis
$redis->set("message", "Hello from Redis!");
echo $redis->get("message"); // Output: Hello from Redis!

?>
