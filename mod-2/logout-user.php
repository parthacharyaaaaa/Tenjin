<?php
session_start();

// Connect to Redis
$redis = new Redis();
$redis->connect('127.0.0.1', 6379);

// Clear Redis session data
$redis->flushAll();

session_unset();
session_destroy();
header('location: login-user.php');
?>