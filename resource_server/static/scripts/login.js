document.addEventListener("DOMContentLoaded", async () => {
    const login = document.getElementById('auth-form');
    if (!login) {
        alert("No login form detected");
    }

    const btn = document.getElementById('submit');
    btn.addEventListener('click', async () => {
        const identity = document.getElementById('identity').value.trim();
        const password = document.getElementById('password').value;

        const isEmail = identity.includes("@");

        if (isEmail) {
            const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
            if (!emailPattern.test(identity)) {
                alert("Please enter a valid email address.");
                return false;
            }
        } else {
            const usernamePattern = /^[a-zA-Z0-9]{6,64}$/;
            if (!usernamePattern.test(identity)) {
                alert("Username must be 8-64 characters and contain no special characters.");
                return false;
            }
        }

        try {
            const response = await fetch('http://127.0.0.1:8000/login', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ identity: identity, password: password }),
                credentials: "include"
            });

            if (!response.ok) {
                const errorText = await response.text(); // Get raw response if not JSON
                throw new Error(`Failed to authenticate. Status: ${response.status} ${response.statusText}\nResponse: ${errorText}`);
            }

            const data = await response.json()

            localStorage.setItem("access_exp", data.access_exp);
            localStorage.setItem("leeway", data.leeway !== undefined ? data.leeway : 0);

            window.location.href = "/";
        }
        catch (error) {
            console.error("Login error:", error);
            alert("Login failed: " + error.message);
        }
    })
})