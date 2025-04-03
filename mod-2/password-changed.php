<?php 
require_once "controllerUserData.php"; 
require 'vendor/autoload.php'; // Ensure Redis is installed via Composer

// Connect to Redis
$redis = new Predis\Client();

// Get session info from Redis
$info = $redis->get('info');

if (!$info) {
    header('Location: login-user.php');  
    exit();
}
?>
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Login Form</title>
    <link rel="stylesheet" href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
    <link rel="stylesheet" href="style.css">
    <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
    <style>
        /* Loading spinner */
        .spinner {
            display: none;
            width: 18px;
            height: 18px;
            border: 3px solid #fff;
            border-top: 3px solid transparent;
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

        .btn-container {
            position: relative;
        }

        /* Countdown Timer */
        .countdown {
            font-size: 14px;
            text-align: center;
            margin-top: 10px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="row">
            <div class="col-md-4 offset-md-4 form login-form">
                <?php if ($info): ?>
                    <div class="alert alert-success text-center">
                        <?php echo $info; ?>
                    </div>
                <?php endif; ?>
                
                <form id="login-form" action="login-user.php" method="POST">
                    <div class="form-group btn-container">
                        <input class="form-control button" id="login-btn" type="submit" name="login-now" value="Login Now">
                        <div class="spinner" id="loading-spinner"></div>
                    </div>
                </form>
                <p class="countdown">Redirecting in <span id="timer">5</span> seconds...</p>
            </div>
        </div>
    </div>

    <script>
        $(document).ready(function () {
            // Countdown timer for redirection
            let countdown = 5;
            let timer = setInterval(function () {
                countdown--;
                $("#timer").text(countdown);
                if (countdown <= 0) {
                    clearInterval(timer);
                    window.location.href = "login-user.php";
                }
            }, 1000);

            // Show loading spinner on button click
            $("#login-form").on("submit", function () {
                $("#login-btn").val("Processing...");
                $("#loading-spinner").show();
                $("#login-btn").prop("disabled", true);
            });
        });
    </script>

</body>
</html>
