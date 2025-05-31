document.addEventListener('DOMContentLoaded', () => {
    const identityField = document.getElementById('identity');
    const passwordField = document.getElementById('password');
    const btn = document.getElementById('recover-account-btn');
    const outputSpam = document.getElementById('output');
    const form = document.getElementById('recover-form-modal');

    btn.addEventListener('click', async () => {
        const identity = identityField.value;
        const password = passwordField.value;

        if (identity === undefined || identity.trim() === '' || password === undefined || password.trim() === ''){
            alert('Please ensure that you fill both fields properly');
            return false;
        }

        try{
            const response = await fetch("/users/recover", {
                method:'PATCH',
                body:JSON.stringify({identity:identity, password:password}),
                headers:{
                    'Content-Type' : 'application/json'
                }
            });

            const data = await response.json();
            outputSpam.innerText = data.message;

            const loginLink = document.createElement('a');
            loginLink.href = data._links.login.href;
            loginLink.innerText = 'Login';
            form.appendChild(loginLink);


            if (!response.ok){
                throw new Error('Failed to recover this acccount');
            }
        }
        catch(error){
            console.error(error);
        }
    });
});