<?php require_once "controllerUserData.php"; ?>

<?php 
// Connect to Redis
$redis = new Redis();
$redis->connect('127.0.0.1', 6379);

$email = $redis->get('email');
if($email == false){
  header('Location: login-user.php');
}
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
        /* Disable button styling */
        .disabled {
            background-color: #ccc !important;
            cursor: not-allowed !important;
        }

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
                <form id="otp-form" action="user-otp.php" method="POST" autocomplete="off">
                    <h2 class="text-center">Code Verification</h2>
                    
                    <?php if(isset($_SESSION['info'])): ?>
                        <div class="alert alert-success text-center">
                            <?php echo $_SESSION['info']; ?>
                        </div>
                    <?php endif; ?>

                    <?php if(count($errors) > 0): ?>
                        <div class="alert alert-danger text-center">
                            <?php foreach($errors as $showerror): ?>
                                <?php echo $showerror; ?>
                            <?php endforeach; ?>
                        </div>
                    <?php endif; ?>

                    <div class="form-group">
                        <input id="otp" class="form-control" type="number" name="otp" placeholder="Enter verification code" required>
                        <small id="otp-error" class="text-danger"></small>
                    </div>

                    <div class="form-group btn-container">
                        <input id="submit-btn" class="form-control button disabled" type="submit" name="check" value="Submit" disabled>
                        <div class="spinner" id="loading-spinner"></div>
                    </div>
                </form>
            </div>
        </div>
    </div>

    <script>
        $(document).ready(function () {
            // Validate OTP input
            $("#otp").on("input", function () {
                var otp = $(this).val();
                if (otp.length === 6 && /^\d{6}$/.test(otp)) {
                    $("#otp-error").text("");
                    $("#submit-btn").removeClass("disabled").prop("disabled", false);
                } else {
                    $("#otp-error").text("OTP must be a 6-digit number.");
                    $("#submit-btn").addClass("disabled").prop("disabled", true);
                }
            });

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
