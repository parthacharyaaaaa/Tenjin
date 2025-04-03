<?php require_once "controllerUserData.php"; ?>
<?php 
// Connect to Redis
$redis = new Redis();
$redis->connect('127.0.0.1', 6379);
?>
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Forgot Password</title>
    <link rel="stylesheet" href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
    <link rel="stylesheet" href="style.css">
    <script>
        function validateEmail() {
            var email = document.getElementById("email").value;
            var emailError = document.getElementById("emailError");
            var emailPattern = /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/;

            if (!emailPattern.test(email)) {
                emailError.innerHTML = "Please enter a valid email address.";
                return false;
            } else {
                emailError.innerHTML = "";
                return true;
            }
        }

        function validateForm(event) {
            if (!validateEmail()) {
                event.preventDefault();
            }
        }
    </script>
</head>
<body>
    <div class="container">
        <div class="row">
            <div class="col-md-4 offset-md-4 form">
                <form action="forgot-password.php" method="POST" autocomplete="" onsubmit="validateForm(event)">
                    <h2 class="text-center">Forgot Password</h2>
                    <p class="text-center">Enter your email address</p>
                    <?php
                        if(count($errors) > 0){
                            ?>
                            <div class="alert alert-danger text-center">
                                <?php 
                                    foreach($errors as $error){
                                        echo $error;
                                    }
                                ?>
                            </div>
                            <?php
                        }
                    ?>
                    <div class="form-group">
                        <input class="form-control" type="email" id="email" name="email" placeholder="Enter email address" required value="<?php echo $email ?>" onkeyup="validateEmail()">
                        <small id="emailError" class="text-danger"></small>
                    </div>
                    <div class="form-group">
                        <input class="form-control button" type="submit" name="check-email" value="Continue">
                    </div>
                </form>
            </div>
        </div>
    </div>
</body>
</html>
