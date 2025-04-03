<?php 
require_once "controllerUserData.php"; 
require 'vendor/autoload.php'; // Ensure Redis is installed via Composer

// Connect to Redis
$redis = new Predis\Client();

// Get session email from Redis instead of $_SESSION
$email = $redis->get('email');

if (!$email) {
    header('Location: login-user.php');
    exit();
}

// Get session info and errors from Redis
$info = $redis->get('info');
$errors = json_decode($redis->get('errors'), true) ?? [];
?>
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Create a New Password</title>
    <link rel="stylesheet" href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
    <link rel="stylesheet" href="style.css">
    <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
    <style>
        /* Password strength indicator */
        .strength {
            font-size: 14px;
            margin-top: 5px;
            display: none;
        }
        .weak { color: red; }
        .medium { color: orange; }
        .strong { color: green; }

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
            <div class="col-md-4 offset-md-4 form">
                <form id="password-form" action="new-password.php" method="POST" autocomplete="off">
                    <h2 class="text-center">New Password</h2>
                    <?php 
                    if ($info) {
                        ?>
                        <div class="alert alert-success text-center">
                            <?php echo $info; ?>
                        </div>
                        <?php
                    }
                    ?>
                    <?php
                    if (count($errors) > 0) {
                        ?>
                        <div class="alert alert-danger text-center">
                            <?php
                            foreach ($errors as $showerror) {
                                echo $showerror . "<br>";
                            }
                            ?>
                        </div>
                        <?php
                    }
                    ?>
                    <div class="form-group">
                        <input id="password" class="form-control" type="password" name="password" placeholder="Create new password" required>
                        <div id="strength-message" class="strength"></div>
                    </div>
                    <div class="form-group">
                        <input id="confirm-password" class="form-control" type="password" name="cpassword" placeholder="Confirm your password" required>
                    </div>
                    <div class="form-group btn-container">
                        <input class="form-control button" id="change-btn" type="submit" name="change-password" value="Change">
                        <div class="spinner" id="loading-spinner"></div>
                    </div>
                </form>
            </div>
        </div>
    </div>

    <script>
        $(document).ready(function () {
            // Password strength check
            $("#password").on("input", function () {
                let password = $(this).val();
                let strengthMessage = $("#strength-message");
                strengthMessage.show();

                if (password.length < 6) {
                    strengthMessage.text("Weak").removeClass().addClass("strength weak");
                } else if (password.match(/[a-zA-Z]/) && password.match(/[0-9]/)) {
                    strengthMessage.text("Medium").removeClass().addClass("strength medium");
                } else if (password.match(/[a-zA-Z]/) && password.match(/[0-9]/) && password.match(/[@$!%*?&]/)) {
                    strengthMessage.text("Strong").removeClass().addClass("strength strong");
                } else {
                    strengthMessage.text("");
                }
            });

            // Form validation and loading spinner
            $("#password-form").on("submit", function (e) {
                let password = $("#password").val().trim();
                let confirmPassword = $("#confirm-password").val().trim();

                if (password === "" || confirmPassword === "") {
                    alert("Please fill in all fields.");
                    e.preventDefault();
                    return;
                }

                if (password !== confirmPassword) {
                    alert("Passwords do not match.");
                    e.preventDefault();
                    return;
                }

                // Show loading spinner and disable button
                $("#change-btn").val("Processing...");
                $("#loading-spinner").show();
                $("#change-btn").prop("disabled", true);
            });
        });
    </script>

</body>
</html>
