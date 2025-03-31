<?php require_once "controllerUserData.php"; ?>
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Signup Form</title>
    <link rel="stylesheet" href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
    <link rel="stylesheet" href="style.css">
</head>
<body>
    <div class="container">
        <div class="row">
            <div class="col-md-4 offset-md-4 form">
                <form action="signup-user.php" method="POST" autocomplete="">
                    <h2 class="text-center">Signup Form</h2>
                    <p class="text-center">It's quick and easy.</p>
                    <?php
                    if(count($errors) == 1){
                        ?>
                        <div class="alert alert-danger text-center">
                            <?php
                            foreach($errors as $showerror){
                                echo $showerror;
                            }
                            ?>
                        </div>
                        <?php
                    }elseif(count($errors) > 1){
                        ?>
                        <div class="alert alert-danger">
                            <?php
                            foreach($errors as $showerror){
                                ?>
                                <li><?php echo $showerror; ?></li>
                                <?php
                            }
                            ?>
                        </div>
                        <?php
                    }
                    ?>
                    <div class="form-group">
                        <input class="form-control" type="text" name="name" placeholder="Full Name" required value="<?php echo $name ?>">
                    </div>
                    <div class="form-group">
                        <input class="form-control" type="email" name="email" placeholder="Email Address" required value="<?php echo $email ?>">
                    </div>
                    <div class="form-group">
                        <input class="form-control" type="password" name="password" placeholder="Password" required>
                    </div>
                    <div class="form-group">
                        <input class="form-control" type="password" name="cpassword" placeholder="Confirm password" required>
                    </div>
                    <div class="form-group">
                        <input class="form-control button" type="submit" name="signup" value="Signup">
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