<?php require_once "controllerUserData.php"; ?>
<?php
if($_SESSION['info'] == false){
    header('Location: login-user.php');  
}
?>
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Login Form</title>
    <link rel="stylesheet" href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
    <link rel="stylesheet" href="style.css">
</head>
<body>
    <div class="container">
        <div class="row">
            <div class="col-md-4 offset-md-4 form login-form">
            <?php 
            if(isset($_SESSION['info'])){
                ?>
                <div class="alert alert-success text-center">
                <?php echo $_SESSION['info']; ?>
                </div>
                <?php
            }
            ?>
                <form action="login-user.php" method="POST">
                    <div class="form-group">
                        <input class="form-control button" type="submit" name="login-now" value="Login Now">
                    </div>
                </form>
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