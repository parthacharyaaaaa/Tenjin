<?php require_once "controllerUserData.php"; ?>
<?php
// // Connect to Redis
// $redis = new Redis();
// $redis->connect('127.0.0.1', 6379);

$email = isset($_SESSION['email']) ? $_SESSION['email'] : "";
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
    </style>
</head>

<body>
    <div class="container">
        <div class="row">
            <div class="col-md-4 offset-md-4 form login-form">
                <form id="login-form" action="login-user.php" method="POST" autocomplete="off">
                    <h2 class="text-center">Login Form</h2>
                    <p class="text-center">Login with your email and password.</p>
                    <?php
                    if (count($errors) > 0) {
                        ?>
                        <div class="alert alert-danger text-center">
                            <?php
                            foreach ($errors as $showerror) {
                                echo $showerror;
                            }
                            ?>
                        </div>
                        <?php
                    }
                    ?> 
                    <div class="form-group">
                        <input id="email" class="form-control" type="email" name="email" placeholder="Email Address" required
                            value="<?php echo $email ?>">
                    </div>
                    <div class="form-group">
                        <input id="password" class="form-control" type="password" name="password" placeholder="Password" required>
                    </div>
                    <div class="link forget-pass text-left"><a href="forgot-password.php">Forgot password?</a></div>
                    <div class="form-group btn-container">
                        <input class="form-control button" id="login-btn" type="submit" name="login" value="Login">
                        <div class="spinner" id="loading-spinner"></div>
                    </div>
                    <div class="link login-link text-center">Not yet a member? <a href="signup-user.php">Signup now</a></div>
                </form>
            </div>
        </div>
    </div>

    <script>
        $(document).ready(function () {
            $("#login-form").on("submit", function (e) {
                let email = $("#email").val().trim();
                let password = $("#password").val().trim();

                if (email === "" || password === "") {
                    alert("Please fill in all fields.");
                    e.preventDefault();
                    return;
                }

                // Show loading spinner and disable button
                $("#login-btn").val("Logging in...");
                $("#loading-spinner").show();
                $("#login-btn").prop("disabled", true);
            });
        });
    </script>

</body>

</html>
