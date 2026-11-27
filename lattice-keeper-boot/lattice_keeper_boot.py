from src.lattice_keeper.secrets import load_genesis_resonance, boot_nonce

nonce = boot_nonce()
secret = load_genesis_resonance()  # already revoked Vault token
keeper = LatticeKeeper(client=get_lattice_client(), namespace="{lattice}")
secret.use(lambda mv: keeper.init_from_seed(bytes(mv)))  # bytes(mv) if keeper wants bytes
secret.wipe()
keeper.start()  # now with Icecast signing from v0.61