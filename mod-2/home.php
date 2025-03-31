<?php require_once "controllerUserData.php"; ?>
<?php
// // Connect to Redis
// $redis = new Redis();
// $redis->connect('127.0.0.1', 6379);

$email = $_SESSION['email'];
$password = $_SESSION['password'];
if ($email != false && $password != false) {
    // Check if user data is cached in Redis
    $user_cache = $redis->get("user:$email");
    if ($user_cache) {
        $fetch_info = json_decode($user_cache, true);
    } else {
        $sql = "SELECT * FROM usertable WHERE email = '$email'";
        $run_Sql = mysqli_query($con, $sql);
        if ($run_Sql) {
            $fetch_info = mysqli_fetch_assoc($run_Sql);
            // Cache user data in Redis for 10 minutes
            $redis->setex("user:$email", 600, json_encode($fetch_info));
        }
    }
    
    if (isset($fetch_info)) {
        $status = $fetch_info['status'];
        $code = $fetch_info['code'];
        if ($status == "verified") {
            if ($code != 0) {
                header('Location: reset-code.php');
            }
        } else {
            header('Location: user-otp.php');
        }
    }
} else {
    header('Location: login-user.php');
}
?>
<!DOCTYPE html>
<html lang="en">

    <head>
        <meta charset="UTF-8">
        <title><?php echo $fetch_info['name'] ?> | Home</title>
        <link rel="stylesheet" href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
        <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
        <style>
            @import url('https://fonts.googleapis.com/css?family=Poppins:400,500,600,700&display=swap');

            nav {
                padding-left: 100px !important;
                padding-right: 100px !important;
                background: #6665ee;
                font-family: 'Poppins', sans-serif;
            }

            nav a.navbar-brand {
                color: #fff;
                font-size: 30px !important;
                font-weight: 500;
            }

            button a {
                color: #6665ee;
                font-weight: 500;
            }

            button a:hover {
                text-decoration: none;
            }

            h1 {
                position: absolute;
                top: 50%;
                left: 50%;
                width: 100%;
                text-align: center;
                transform: translate(-50%, -50%);
                font-size: 50px;
                font-weight: 600;
                opacity: 0;
            }

            #logout-btn {
                position: relative;
            }

            #loading-spinner {
                display: none;
                width: 18px;
                height: 18px;
                border: 3px solid #6665ee;
                border-top: 3px solid #fff;
                border-radius: 50%;
                animation: spin 0.8s linear infinite;
                position: absolute;
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%);
            }

            @keyframes spin {
                0% { transform: translate(-50%, -50%) rotate(0deg); }
                100% { transform: translate(-50%, -50%) rotate(360deg); }
            }
        </style>
    </head>

    <body>
        <nav class="navbar">
            <a class="navbar-brand" href="#">Tenjin</a>
            <button type="button" class="btn btn-light" id="logout-btn">
                <span id="loading-spinner"></span>
                <a href="logout-user.php">Logout</a>
            </button>
        </nav>
        <h1 id="welcome-message">Welcome <?php echo $fetch_info['name'] ?></h1>

        <script>
            // Smooth fade-in for welcome message
            $(document).ready(function () {
                $("#welcome-message").fadeTo(1000, 1);
            });

            // Show loading spinner on logout click
            document.getElementById("logout-btn").addEventListener("click", function () {
                document.getElementById("loading-spinner").style.display = "inline-block";
            });
        </script>
    </body>

</html>
