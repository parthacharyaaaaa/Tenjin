document.addEventListener('DOMContentLoaded', () => {
    const digest = window.location.pathname.split("/")[2];
    const btn = document.getElementById('change-password-btn');
    const passwordField = document.getElementById('password');
    const cpasswordField = document.getElementById('cpassword');
    const outputSpan = document.getElementById('output');

    btn.addEventListener('click', async () => {
        const password = passwordField.value;
        const cpassword = cpasswordField.value;

        if (password != cpassword){
            alert('Passwords do not match');
            return false;
        }

        try{
            const response = await fetch(`/users/update-password/${digest}`, {
                method:'PATCH',
                body:JSON.stringify({password:password, cpassword:cpassword}),
                headers:{
                    'Content-Type' : 'application/json'
                }
            });
            if (!response.ok){
                throw new Error('Failed to update password');
            }

            const data = await response.json();
            outputSpan.innerText = data.message;
        }
        catch(error){
            console.error(error)
        }
    });
});