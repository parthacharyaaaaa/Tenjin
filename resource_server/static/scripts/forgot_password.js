document.addEventListener('DOMContentLoaded', () => {
    const recoveryButton = document.getElementById('recover-pass-btn');
    const identityField = document.getElementById('identity');
    const outputSpan = document.getElementById('output');
    recoveryButton.addEventListener('click', async () => {
        const identity = identityField.value;
        try{
            const response = await fetch('/users/recover-password', {
                method:'POST',
                body:JSON.stringify({identity:identity}),
                headers : {
                    'Content-Type' : 'application/json'
                }
            });

            if (!response.ok){
                throw new Error('Failed to request password recovery');
            }

            const data = await response.json()
            outputSpan.innerText = data.message;
        }
        catch(error){
            console.error(error);
        }
    });
});