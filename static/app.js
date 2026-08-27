const tabs =
    document.querySelectorAll('.tab');

const panels =
    document.querySelectorAll('.panel');

const statusEl =
    document.getElementById('status');

const outputEl =
    document.getElementById('output');

const downloadsEl =
    document.getElementById('downloads');

const facecheckInput =
    document.getElementById('facecheck-image');

const facecheckPreview =
    document.getElementById('facecheck-preview');


tabs.forEach((tab) => {

    tab.addEventListener('click', () => {

        tabs.forEach((item) => {
            item.classList.remove('active');
        });

        panels.forEach((panel) => {
            panel.classList.remove('active');
        });

        tab.classList.add('active');

        document
            .getElementById(tab.dataset.tab)
            .classList.add('active');

    });

});


function setLoading(message) {

    statusEl.textContent = message;

    outputEl.textContent = '';

    downloadsEl.innerHTML = '';

}


function showResponse(data) {

    if (!data.ok) {

        statusEl.textContent =
            'Erro na consulta.';

        outputEl.textContent =
            data.erro || 'Erro desconhecido.';

        return;

    }

    statusEl.textContent =
        data.arquivo
            ? `Relatório gerado: ${data.arquivo}`
            : data.mensagem || 'Operação concluída.';

    outputEl.textContent =
        JSON.stringify(
            data.relatorio || data,
            null,
            2
        );

    const links = [];

    if (data.download) {

        links.push(
            `<a href="${data.download}">
                Baixar JSON
            </a>`
        );

    }

    if (data.integrado) {

        links.push(
            `<a href="${data.integrado}">
                Baixar relatório integrado
            </a>`
        );

    }

    downloadsEl.innerHTML =
        links.join('');

}


async function postJson(url, payload) {

    const response = await fetch(
        url,
        {
            method: 'POST',

            headers: {
                'Content-Type':
                    'application/json'
            },

            body: JSON.stringify(payload),
        }
    );

    const data =
        await response.json();

    showResponse(data);

}


document
    .getElementById('osint-form')
    .addEventListener(
        'submit',
        async (event) => {

            event.preventDefault();

            setLoading(
                'Executando osint.py...'
            );

            try {

                await postJson(
                    '/api/osint',
                    {
                        nome:
                            document
                                .getElementById('nome')
                                .value,

                        cpf:
                            document
                                .getElementById('cpf')
                                .value,
                    }
                );

            } catch (error) {

                showResponse({
                    ok: false,
                    erro: error.message
                });

            }

        }
    );


document
    .getElementById('maigret-form')
    .addEventListener(
        'submit',
        async (event) => {

            event.preventDefault();

            setLoading(
                'Executando Maigret...'
            );

            try {

                await postJson(
                    '/api/maigret',
                    {
                        username:
                            document
                                .getElementById('username')
                                .value,
                    }
                );

            } catch (error) {

                showResponse({
                    ok: false,
                    erro: error.message
                });

            }

        }
    );


if (facecheckInput && facecheckPreview) {

    facecheckInput.addEventListener(
        'change',
        () => {

            const file =
                facecheckInput.files[0];

            if (!file) {

                facecheckPreview.removeAttribute('src');

                facecheckPreview.style.display =
                    'none';

                return;

            }

            const imageUrl =
                URL.createObjectURL(file);

            facecheckPreview.src =
                imageUrl;

            facecheckPreview.style.display =
                'block';

        }
    );

}


const facecheckForm =
    document.getElementById(
        'facecheck-form'
    );


if (facecheckForm) {

    facecheckForm.addEventListener(
        'submit',
        async (event) => {

            event.preventDefault();

            const file =
                facecheckInput.files[0];

            if (!file) {

                showResponse({
                    ok: false,
                    erro: 'Selecione uma imagem.'
                });

                return;

            }

            setLoading(
                'Preparando imagem...'
            );

            const formData =
                new FormData();

            formData.append(
                'imagem',
                file
            );

            try {

                const response =
                    await fetch(
                        '/api/facecheck/preparar',
                        {
                            method: 'POST',
                            body: formData
                        }
                    );

                const data =
                    await response.json();

                showResponse(data);

            } catch (error) {

                showResponse({
                    ok: false,
                    erro: error.message
                });

            }

        }
    );

}