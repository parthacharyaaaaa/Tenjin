<?php require_once "controllerUserData.php"; ?>
<?php 
$email = $_SESSION['email'];
if($email == false){
  header('Location: login-user.php');
}
?>
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Create a New Password</title>
    <link rel="stylesheet" href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
    <link rel="stylesheet" href="style.css">
</head>
<body>
    <div class="container">
        <div class="row">
            <div class="col-md-4 offset-md-4 form">
                <form action="new-password.php" method="POST" autocomplete="off">
                    <h2 class="text-center">New Password</h2>
                    <?php 
                    if(isset($_SESSION['info'])){
                        ?>
                        <div class="alert alert-success text-center">
                            <?php echo $_SESSION['info']; ?>
                        </div>
                        <?php
                    }
                    ?>
                    <?php
                    if(count($errors) > 0){
                        ?>
                        <div class="alert alert-danger text-center">
                            <?php
                            foreach($errors as $showerror){
                                echo $showerror;
                            }
                            ?>
                        </div>
                        <?php
                    }
                    ?>
                    <div class="form-group">
                        <input class="form-control" type="password" name="password" placeholder="Create new password" required>
                    </div>
                    <div class="form-group">
                        <input class="form-control" type="password" name="cpassword" placeholder="Confirm your password" required>
                    </div>
                    <div class="form-group">
                        <input class="form-control button" type="submit" name="change-password" value="Change">
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