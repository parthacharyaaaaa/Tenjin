 <?php
require_once "controllerUserData.php";

// Include Redis
$redis = new Redis();
$redis->connect('127.0.0.1', 6379);

// Retrieve session values from Redis
$email = $redis->get('email') ?? '';
$name = $redis->get('name') ?? '';
 ?> 

<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Signup Form</title>
    <link rel="stylesheet" href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
    <link rel="stylesheet" href="style.css">
    <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>

    <style>
        /* Password Strength Indicator */
        .strength {
            text-align: center;
            font-weight: bold;
            margin-top: 5px;
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
                <form id="signup-form" action="signup-user.php" method="POST" autocomplete="">
                    <h2 class="text-center">Signup Form</h2>
                    <p class="text-center">It's quick and easy.</p>

                    <?php if(count($errors) > 0): ?>
                        <div class="alert alert-danger">
                            <ul>
                                <?php foreach($errors as $showerror): ?>
                                    <li><?php echo $showerror; ?></li>
                                <?php endforeach; ?>
                            </ul>
                        </div>
                    <?php endif; ?>

                    <div class="form-group">
                        <input class="form-control" type="text" name="name" placeholder="Full Name" required value="<?php echo htmlspecialchars($name); ?>">
                    </div>
                    <div class="form-group">
                        <input class="form-control" type="email" name="email" placeholder="Email Address" required value="<?php echo htmlspecialchars($email); ?>">
                    </div>
                    <div class="form-group">
                        <input id="password" class="form-control" type="password" name="password" placeholder="Password" required>
                        <div id="password-strength" class="strength"></div>
                    </div>
                    <div class="form-group">
                        <input id="cpassword" class="form-control" type="password" name="cpassword" placeholder="Confirm password" required>
                        <div id="password-match" class="strength"></div>
                    </div>
                    <div class="form-group btn-container">
                        <input id="submit-btn" class="form-control button" type="submit" name="signup" value="Signup">
                        <div class="spinner" id="loading-spinner"></div>
                    </div>
                    <div class="link login-link text-center">Already a member? <a href="login-user.php">Login here</a></div>
                </form>
            </div>
        </div>
    </div>

    <script>
        $(document).ready(function () {
            // Password Strength Checker
            $("#password").on("input", function () {
                var password = $(this).val();
                var strengthText = "";
                var strengthClass = "";

                if (password.length < 6) {
                    strengthText = "Weak (min 6 chars)";
                    strengthClass = "weak";
                } else if (password.match(/[A-Z]/) && password.match(/[0-9]/)) {
                    strengthText = "Medium (Add symbols for strength)";
                    strengthClass = "medium";
                } else if (password.match(/[A-Z]/) && password.match(/[0-9]/) && password.match(/[@$!%*?&]/)) {
                    strengthText = "Strong";
                    strengthClass = "strong";
                } else {
                    strengthText = "Weak (Use uppercase, numbers, symbols)";
                    strengthClass = "weak";
                }

                $("#password-strength").text(strengthText).removeClass("weak medium strong").addClass(strengthClass);
            });

            // Confirm Password Validation
            $("#cpassword").on("input", function () {
                var password = $("#password").val();
                var confirmPassword = $(this).val();

                if (password !== confirmPassword) {
                    $("#password-match").text("Passwords do not match!").addClass("weak");
                } else {
                    $("#password-match").text("Passwords match!").removeClass("weak").addClass("strong");
                }
            });

            // Show loading spinner on form submit
            $("#signup-form").on("submit", function () {
                $("#submit-btn").val("Processing...");
                $("#loading-spinner").show();
                $("#submit-btn").prop("disabled", true);
            });
        });
    </script>

</body>
</html>
