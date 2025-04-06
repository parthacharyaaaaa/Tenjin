document.addEventListener("DOMContentLoaded", async () => {
    const login = document.getElementById('auth-form');
    if (!login){
        alert("No login form detected");
    }

    const btn = document.getElementById('submit');
    btn.addEventListener('click', async () => {
        const username = document.getElementById('username').value;
        const email = document.getElementById('email').value;
        const password = document.getElementById('password').value;
        const cpassword = document.getElementById('cpassword').value;
        const alias = document.getElementById('alias').value;

        // Some validation logic

        try{
            const response = await fetch('http://192.168.0.103:8000/signup', {
                method : 'POST',
                headers : {
                    'Content-Type' : 'application/json'
                },
                body : JSON.stringify({identity:identity, password:password, cpassword:cpassword, alias:alias, email:email}),
                credentials: 'include'
            });

            if (!response.ok){
                const errorText = await response.text();
                throw new Error(`Failed to authenticate. Status: ${response.status} ${response.statusText}\nResponse: ${errorText}`);
            }

            const data = await response.json()

            localStorage.setItem("access_exp", data.access_exp);
            localStorage.setItem("leeway", data.leeway !== undefined ? data.leeway : 0);

            window.location.href = "/";
        }
        catch(error){
            console.error("Signup error:", error);
            alert("Registration failed: " + error.message);
        }
    })
})