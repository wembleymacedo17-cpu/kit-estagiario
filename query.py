


COLABORADOR="""
SELECT 
    a.numcad AS cracha,
    a.nomfun AS nome,
    LPAD(TO_CHAR(a.numcpf), 11, '0') AS cpf,
    TO_CHAR(a.datnas, 'YYYY-MM-DD') AS data_nascimento,
    b.dessit AS descricao_situacao,
    car.codcar AS id_cargo,
    car.titcar AS titulo_reduzido_cargo,
    a.datafa AS data_demissao
FROM r034fun a
LEFT JOIN r010sit b 
    ON a.sitafa = b.codsit
LEFT JOIN r038hca hca
    ON a.numemp = hca.numemp
   AND a.tipcol = hca.tipcol
   AND a.numcad = hca.numcad
   AND hca.datalt = (
       SELECT MAX(hca2.datalt)
       FROM r038hca hca2
       WHERE hca2.numemp = hca.numemp
         AND hca2.tipcol = hca.tipcol
         AND hca2.numcad = hca.numcad
   )
LEFT JOIN r024car car
    ON car.estcar = hca.estcar
   AND car.codcar = hca.codcar
WHERE a.numemp = 1 
  AND a.tipcol = 1
ORDER BY a.nomfun
"""

################################################################################# DAODOS GLOBAIS 

busca_suba = """SELECT cracha , nome_funcionario, situacao, descricao_situacao FROM colaboradores  """


CARGOS_REJEITADO = [ '1013', '11', '1159', '1161', '12', '125', '135', '137', '156', '157', 
'158', '161', '162', '171', '172', '173', '174', '175', '176', '177', 
'178', '179', '180', '181', '182', '184', '194', '244', '245', '246', 
'247', '248', '249', '251', '257', '258', '259', '260', '261', '262', 
'263', '264', '265', '267', '269', '270', '271', '273', '274', '279', 
'280', '282', '283', '286', '287', '288', '291', '294', '295', '297', 
'298', '300', '301', '307', '308', '309', '310', '311', '312', '313', 
'314', '316', '317', '319', '322', '323', '326', '348', '349', '359', 
'453', '48', '49', '497', '499', '50', '51', '518', '519', '52', 
'520', '53', '530', '54', '55', '56', '57', '66', '666', '789', 
'799', '8020', '8026', '807', '808', '809', '823', '837', '840', '857', 
'858', '875', '9000', '9042', '910', '921', '933', '935', '936', '940', 
'943', '944', '945', '948', '953', '954', '955', '956', '957', '958', 
'959', '960', '961', '962', '963', '964', '965', '968', '975', '976', 
'977', '978', '979', '980', '981', '982', '983', '984', '985', '987', 
'988', '989', '993', '994', '995', '996', '997', '998']


DOMINIOS_PESSOAIS_PERMITIDOS = {
    "gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "yahoo.com.br",
    "icloud.com", "live.com", "bol.com.br", "uol.com.br", "terra.com.br",
    "ig.com.br", "r7.com", "globo.com", "msn.com",
}

MODELOS_GEMINI = [
    "gemini-3.1-flash-lite",
    "gemini-3.6-flash",
    "gemini-2.5-flash"
]

ERROS_RETRY = ("503", "unavailable", "timeout", "timed out", "429", "high demand", "disconnected", "remoteprotocolerror", "reset")   



TAMANHO_MAXIMO_MB = 10
EXTENSOES_PERMITIDAS = ["pdf", "png", "jpg", "jpeg"]
