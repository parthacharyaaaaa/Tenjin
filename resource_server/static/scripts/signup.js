document.addEventListener("DOMContentLoaded", async () => {

    const btn = document.getElementById('sign-up');
    btn.addEventListener('click', async () => {
        const username = document.getElementById('username').value.trim();
        const email = document.getElementById('email').value.trim();
        const password = document.getElementById('password').value;
        const cpassword = document.getElementById('cpassword').value;
        const alias = document.getElementById('alias').value.trim();

        if (!/^[a-zA-Z0-9]{8,64}$/.test(username)) {
            alert("Username must be 8-64 characters with no special characters.");
            return false;
        }

        if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
            alert("Please enter a valid email.");
            return false;
        }

        if (password.length < 8) {
            alert("Password must be at least 8 characters.");
            return false;
        }

        if (password !== cpassword){
            alert("Passwords do not match");
            return false;
        }

        alert('here')

        try {
            const response = await fetch('http://127.0.0.1:8000/register', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ username: username, email:email, password: password, cpassword: cpassword, alias: alias, email: email }),
                credentials: 'include'
            });

            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`Failed to authenticate. Status: ${response.status} ${response.statusText}\nResponse: ${errorText}`);
            }

            const data = await response.json()

            localStorage.setItem("access_exp", data.access_exp);
            localStorage.setItem("leeway", data.leeway !== undefined ? data.leeway : 0);

            window.location.href = "/";
        }
        catch (error) {
            console.error("Signup error:", error);
            alert("Registration failed: " + error.message);
        }
    })
})