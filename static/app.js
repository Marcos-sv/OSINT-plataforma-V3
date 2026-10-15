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

const bnmpHandoff =
    document.getElementById('bnmp-handoff');

const bnmpHandoffMessage =
    document.getElementById('bnmp-handoff-message');

const bnmpContinueButton =
    document.getElementById('bnmp-continue');

let activeBnmpJob = null;


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
        statusEl.textContent = 'Erro na consulta.';
        outputEl.textContent =
            data.erro || 'Erro desconhecido.';
        return;
    }

    statusEl.textContent =
        data.mensagem || (data.arquivo
            ? `Relatório gerado: ${data.arquivo}`
            : 'Operação concluída.');

    outputEl.textContent = JSON.stringify(
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

    downloadsEl.innerHTML = links.join('');
}


async function postJson(url, payload) {
    const response = await fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload),
    });

    const data = await response.json();
    showResponse(data);
}


function pause(milliseconds) {
    return new Promise((resolve) => {
        setTimeout(resolve, milliseconds);
    });
}


async function readJsonResponse(response) {
    const data = await response.json();

    if (!response.ok || !data.ok) {
        throw new Error(
            data.erro || `Falha HTTP ${response.status}`
        );
    }

    return data;
}


async function runBnmpForOsint(osintData, query) {
    bnmpHandoff.hidden = true;
    activeBnmpJob = osintData.bnmp_tarefa_id || null;

    try {
        // Compatibilidade com app.py anterior, que não devolvia o ID BNMP.
        if (!activeBnmpJob && !osintData.bnmp_erro) {
            const startResponse = await fetch(
                '/api/bnmp/iniciar',
                {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        ...query,
                        osint_arquivo: osintData.arquivo,
                    }),
                }
            );

            const started =
                await readJsonResponse(startResponse);

            activeBnmpJob =
                started.tarefa_id || null;
        }

        if (!activeBnmpJob) {
            const reason =
                osintData.bnmp_erro ||
                'O BNMP não foi iniciado nesta execução.';

            showResponse({
                ok: true,
                mensagem:
                    `OSINT concluído; BNMP não iniciou: ${reason}`,
                relatorio: {
                    osint: osintData.relatorio,
                    bnmp: {
                        status: 'consulta_nao_iniciada',
                        erro: reason,
                    },
                },
                arquivo: osintData.arquivo,
                download: osintData.download,
                integrado: osintData.integrado,
            });

            return;
        }

        while (activeBnmpJob) {
            const response = await fetch(
                `/api/bnmp/${encodeURIComponent(activeBnmpJob)}`
            );

            const job =
                await readJsonResponse(response);

            statusEl.textContent =
                job.mensagem || 'Consultando o BNMP...';

            if (job.status === 'aguardando_captcha') {
                bnmpHandoffMessage.textContent =
                    job.mensagem;

                bnmpHandoff.hidden = false;

            } else if (job.status === 'concluida') {
                bnmpHandoff.hidden = true;

                showResponse({
                    ok: true,
                    mensagem: 'Busca OSINT + BNMP concluída.',
                    relatorio: job.relatorio || {
                        ...osintData.relatorio,
                        fontes_complementares: {
                            ...(osintData.relatorio.fontes_complementares || {}),
                            bnmp: job.bnmp_relatorio,
                        },
                    },
                    arquivo: job.arquivo,
                    download: job.download,
                    integrado: job.integrado,
                });

                activeBnmpJob = null;
                break;

            } else if (
                job.status === 'erro' ||
                job.status === 'expirada'
            ) {
                bnmpHandoff.hidden = true;

                showResponse({
                    ok: true,
                    mensagem:
                        `OSINT concluído; BNMP não concluiu: ${job.mensagem}`,
                    relatorio: job.relatorio || {
                        ...osintData.relatorio,
                        fontes_complementares: {
                            ...(osintData.relatorio.fontes_complementares || {}),
                            bnmp: {
                                status: 'consulta_nao_concluida',
                                erro: job.mensagem,
                            },
                        },
                    },
                    arquivo:
                        job.arquivo || osintData.arquivo,
                    download:
                        job.download || osintData.download,
                    integrado:
                        job.integrado || osintData.integrado,
                });

                activeBnmpJob = null;
                break;
            }

            await pause(900);
        }

    } catch (error) {
        bnmpHandoff.hidden = true;
        activeBnmpJob = null;

        showResponse({
            ok: true,
            mensagem:
                `OSINT concluído; BNMP não concluiu: ${error.message}`,
            relatorio: {
                osint: osintData.relatorio,
                bnmp: {
                    status: 'consulta_nao_concluida',
                    erro: error.message,
                },
            },
            arquivo: osintData.arquivo,
            download: osintData.download,
            integrado: osintData.integrado,
        });
    }
}


if (bnmpContinueButton) {
    bnmpContinueButton.addEventListener(
        'click',
        async () => {
            if (!activeBnmpJob) return;

            bnmpContinueButton.disabled = true;
            statusEl.textContent = 'Retomando a consulta...';

            try {
                const response = await fetch(
                    `/api/bnmp/${encodeURIComponent(activeBnmpJob)}/continuar`,
                    {method: 'POST'}
                );

                await readJsonResponse(response);
                bnmpHandoff.hidden = true;

            } catch (error) {
                showResponse({
                    ok: false,
                    erro: error.message
                });

            } finally {
                bnmpContinueButton.disabled = false;
            }
        }
    );
}


document
    .getElementById('osint-form')
    .addEventListener(
        'submit',
        async (event) => {
            event.preventDefault();

            setLoading('Executando fontes OSINT...');

            try {
                const query = {
                    nome:
                        document.getElementById('nome').value,
                    cpf:
                        document.getElementById('cpf').value,
                };

                const response = await fetch(
                    '/api/osint',
                    {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify(query),
                    }
                );

                const osintData =
                    await readJsonResponse(response);

                await runBnmpForOsint(osintData, query);

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

            setLoading('Executando Maigret...');

            try {
                await postJson(
                    '/api/maigret',
                    {
                        username:
                            document.getElementById('username').value,
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
            const file = facecheckInput.files[0];

            if (!file) {
                facecheckPreview.removeAttribute('src');
                facecheckPreview.style.display = 'none';
                return;
            }

            const imageUrl = URL.createObjectURL(file);

            facecheckPreview.src = imageUrl;
            facecheckPreview.style.display = 'block';
        }
    );
}


const facecheckForm =
    document.getElementById('facecheck-form');


if (facecheckForm) {
    facecheckForm.addEventListener(
        'submit',
        async (event) => {
            event.preventDefault();

            const file = facecheckInput.files[0];

            if (!file) {
                showResponse({
                    ok: false,
                    erro: 'Selecione uma imagem.'
                });
                return;
            }

            setLoading('Preparando imagem...');

            const formData = new FormData();
            formData.append('imagem', file);

            try {
                const response = await fetch(
                    '/api/facecheck/preparar',
                    {
                        method: 'POST',
                        body: formData
                    }
                );

                const data = await response.json();
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