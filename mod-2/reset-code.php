<?php 
require_once "controllerUserData.php"; 
require 'vendor/autoload.php'; // Ensure you have Redis installed via Composer

// // Connect to Redis
// $redis = new Predis\Client();

// // Get email from Redis instead of session
// $email = $redis->get('email');

// if (!$email) {
//     header('Location: login-user.php');
//     exit();
// }
?> 
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Code Verification</title>
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

        /* OTP Fields */
        .otp-input {
            width: 40px;
            height: 40px;
            text-align: center;
            font-size: 20px;
            margin: 5px;
            border: 1px solid #ccc;
            border-radius: 5px;
        }

    </style>
</head>
<body>
    <div class="container">
        <div class="row">
            <div class="col-md-4 offset-md-4 form">
                <form id="otp-form" action="reset-code.php" method="POST" autocomplete="off">
                    <h2 class="text-center">Code Verification</h2>
                    <?php 
                    $info = $redis->get('info');
                    if ($info): ?>
                        <div class="alert alert-success text-center" style="padding: 0.4rem 0.4rem">
                            <?php echo $info; ?>
                        </div>
                    <?php endif; ?>

                    <?php
                    $errors = $redis->lrange('errors', 0, -1); // Get all errors from Redis
                    if (count($errors) > 0): ?>
                        <div class="alert alert-danger text-center">
                            <?php foreach ($errors as $showerror) echo $showerror; ?>
                        </div>
                    <?php endif; ?>

                    <!-- OTP Fields -->
                    <div class="form-group text-center">
                        <input class="otp-input" type="text" maxlength="1" oninput="moveNext(this, 1)">
                        <input class="otp-input" type="text" maxlength="1" oninput="moveNext(this, 2)">
                        <input class="otp-input" type="text" maxlength="1" oninput="moveNext(this, 3)">
                        <input class="otp-input" type="text" maxlength="1" oninput="moveNext(this, 4)">
                        <input class="otp-input" type="text" maxlength="1" oninput="moveNext(this, 5)">
                        <input class="otp-input" type="text" maxlength="1" oninput="moveNext(this, 6)">
                        <input type="hidden" id="otp" name="otp">
                    </div>

                    <div class="form-group btn-container">
                        <input class="form-control button" id="submit-btn" type="submit" name="check-reset-otp" value="Submit">
                        <div class="spinner" id="loading-spinner"></div>
                    </div>
                </form>
            </div>
        </div>
    </div>

    <script>
        // Move to the next input field on keypress
        function moveNext(el, index) {
            let inputs = document.querySelectorAll('.otp-input');
            let otpValue = '';

            // Capture OTP value
            inputs.forEach(input => otpValue += input.value);
            document.getElementById('otp').value = otpValue;

            if (el.value.length === 1 && index < inputs.length) {
                inputs[index].focus();
            }
        }

        $(document).ready(function () {
            // Show loading spinner on form submit
            $("#otp-form").on("submit", function () {
                $("#submit-btn").val("Processing...");
                $("#loading-spinner").show();
                $("#submit-btn").prop("disabled", true);
            });
        });
    </script>

</body>
</html>
