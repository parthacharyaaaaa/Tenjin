<?php 
session_start();
require "connection.php";
$email = "";
$name = "";
$errors = array();

// Connect to Redis
// $redis = new Redis();
// $redis->connect('127.0.0.1', 6379);

//if user signup button
if(isset($_POST['signup'])){
    $name = mysqli_real_escape_string($con, $_POST['name']);
    $email = mysqli_real_escape_string($con, $_POST['email']);
    $password = mysqli_real_escape_string($con, $_POST['password']);
    $cpassword = mysqli_real_escape_string($con, $_POST['cpassword']);
    if($password !== $cpassword){
        $errors['password'] = "Confirm password not matched!";
    }
    $email_check = "SELECT * FROM usertable WHERE email = '$email'";
    $res = mysqli_query($con, $email_check);
    if(mysqli_num_rows($res) > 0){
        $errors['email'] = "Email that you have entered is already exist!";
    }
    if(count($errors) === 0){
        $encpass = password_hash($password, PASSWORD_BCRYPT);
        $code = rand(999999, 111111);
        $status = "notverified";
        $insert_data = "INSERT INTO usertable (name, email, password, code, status)
                        values('$name', '$email', '$encpass', '$code', '$status')";
        $data_check = mysqli_query($con, $insert_data);
        if($data_check){
            $redis->set("user:$email", json_encode(["name" => $name, "status" => $status]));
            
            $subject = "Account Verification";
            $message = "Your account is verified .";
            $sender = "From: hillonishah06@gmail.com";
            if(mail($email, $subject, $message, $sender)){
                $_SESSION['info'] = "We've sent a verification link to your email - $email";
                $_SESSION['email'] = $email;
                $_SESSION['password'] = $password;
                header('location: user-otp.php');
                exit();
            }else{
                $errors['otp-error'] = "Verification Failed!";
            }
        }else{
            $errors['db-error'] = "Failed while inserting data into database!";
        }
    }
}

//if user click login button
if(isset($_POST['login'])){
    $email = mysqli_real_escape_string($con, $_POST['email']);
    $password = mysqli_real_escape_string($con, $_POST['password']);
    
    // Check Redis Cache
    if ($redis->exists("user:$email")) {
        $user_data = json_decode($redis->get("user:$email"), true);
        $status = $user_data['status'];
    } else {
        $check_email = "SELECT * FROM usertable WHERE email = '$email'";
        $res = mysqli_query($con, $check_email);
        if(mysqli_num_rows($res) > 0){
            $fetch = mysqli_fetch_assoc($res);
            $status = $fetch['status'];
            $redis->set("user:$email", json_encode(["name" => $fetch['name'], "status" => $status]));
        } else {
            $errors['email'] = "It's look like you're not yet a member! Click on the bottom link to signup.";
        }
    }
    
    if(isset($status) && $status == 'verified'){
        $_SESSION['email'] = $email;
        header('location: home.php');
    }else{
        $_SESSION['info'] = "It looks like you haven't verified your email - $email";
        header('location: user-otp.php');
    }
}
?>