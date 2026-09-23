"""Jeu de démonstration déterministe, transactionnel et réexécutable sans doublons."""

import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import Base, make_engine
from app.models import Agency, Offer, Product, Supplier, SupplierProduct

# nom, catégorie, unité PMC, prix de référence HT (chaînes -> Decimal uniquement)
CATALOG = [
    ("Plaque BA13 2500 × 1200 × 13 mm", "Plâtrerie", "plaque", "9.80"),
    ("Rail R48 3 m", "Plâtrerie", "pièce", "3.20"),
    ("Montant M48 2,5 m", "Plâtrerie", "pièce", "4.10"),
    ("Laine de verre 100 mm", "Isolation", "m²", "6.50"),
    ("Enduit à joint 25 kg", "Plâtrerie", "sac", "24.90"),
    ("Ciment 25 kg", "Gros œuvre", "sac", "7.40"),
    ("Vis placo 3,5 × 25 mm", "Fixation", "vis", "0.03"),
    ("Bande à joint papier", "Plâtrerie", "m", "0.12"),
    ("Tube PER diamètre 16 mm", "Plomberie", "m", "1.25"),
    ("Raccord PER droit 16 mm", "Plomberie", "pièce", "2.80"),
    ("Tube PVC évacuation 100 mm", "Plomberie", "m", "5.30"),
    ("Coude PVC 100 mm 90°", "Plomberie", "pièce", "4.60"),
    ("Mortier 25 kg", "Gros œuvre", "sac", "8.70"),
    ("Parpaing creux 20 × 20 × 50 cm", "Gros œuvre", "pièce", "1.85"),
    ("Sable 35 kg", "Gros œuvre", "sac", "4.30"),
    ("Colle carrelage 25 kg", "Carrelage", "sac", "18.90"),
    ("Joint carrelage gris 5 kg", "Carrelage", "sac", "12.50"),
    ("Panneau OSB 3 2500 × 1250 × 18 mm", "Bois", "panneau", "32.00"),
    ("Tasseau sapin 27 × 27 mm 2 m", "Bois", "pièce", "3.80"),
    ("Mastic acrylique blanc 310 ml", "Finition", "cartouche", "3.10"),
]
SNAPSHOT_DATE = datetime(2026, 1, 15, 8, 0, tzinfo=timezone.utc)
AGENCIES = [
    ("Paris Est", "12 rue des Ateliers fictifs", "75012", "Paris", "48.8400", "2.4100"),
    ("Ivry", "8 allée des Bâtisseurs fictifs", "94200", "Ivry-sur-Seine", "48.8100", "2.3900"),
    ("Saint-Denis", "6 voie des Matériaux fictifs", "93200", "Saint-Denis", "48.9300", "2.3600"),
]


def seed(session: Session) -> None:
    # PostgreSQL : sérialise les seeds en cas de démarrages concurrents.
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy import text

        session.execute(text("SELECT pg_advisory_xact_lock(710031)"))
    for index, (name, category, unit, base_price) in enumerate(CATALOG, start=1):
        code = f"PMC{index:04d}"
        product = session.scalar(select(Product).where(Product.code == code))
        if product is None:
            product = Product(
                code=code,
                name=name,
                category=category,
                reference_unit=unit,
                description="Matériau de démonstration — caractéristiques simulées.",
            )
            session.add(product)
            session.flush()
        for supplier_index, supplier_name in enumerate(["POINT.P TEST", "GEDIMAT TEST"]):
            supplier = session.scalar(select(Supplier).where(Supplier.name == supplier_name))
            if supplier is None:
                supplier = Supplier(name=supplier_name)
                session.add(supplier)
                session.flush()
            reference = f"{'PPT' if supplier_index == 0 else 'GDT'}-{index * 137:05d}"
            sp = session.scalar(
                select(SupplierProduct).where(
                    SupplierProduct.supplier_id == supplier.id,
                    SupplierProduct.supplier_reference == reference,
                )
            )
            pack = {4: (6, 8), 7: (100, 200), 8: (150, 75), 9: (25, 50), 11: (4, 3)}.get(
                index, (1, 1)
            )[supplier_index]
            if sp is None:
                sp = SupplierProduct(
                    product_id=product.id,
                    supplier_id=supplier.id,
                    supplier_reference=reference,
                    designation=f"{name} — gamme {'PPT' if supplier_index == 0 else 'GDT'}",
                    supplier_unit=unit if pack == 1 else f"lot de {pack} {unit}",
                    reference_quantity=Decimal(pack),
                    active=True,
                )
                session.add(sp)
                session.flush()
            for agency_index, (label, address, postal, city, lat, lon) in enumerate(AGENCIES):
                agency_name = f"{supplier_name} · {label}"
                agency = session.scalar(
                    select(Agency).where(
                        Agency.supplier_id == supplier.id,
                        Agency.name == agency_name,
                    )
                )
                if agency is None:
                    agency = Agency(
                        supplier_id=supplier.id,
                        name=agency_name,
                        address=address,
                        postal_code=postal,
                        city=city,
                        latitude=Decimal(lat) + Decimal(supplier_index) * Decimal("0.006"),
                        longitude=Decimal(lon) + Decimal(supplier_index) * Decimal("0.008"),
                    )
                    session.add(agency)
                    session.flush()
                existing = session.scalar(
                    select(Offer).where(
                        Offer.supplier_product_id == sp.id,
                        Offer.agency_id == agency.id,
                    )
                )
                if existing is None:
                    # Les fournisseurs alternent l'avantage prix selon le produit.
                    factor = Decimal("0.92") if index % 2 == supplier_index else Decimal("1.07")
                    price = (
                        Decimal(base_price)
                        * pack
                        * (factor + Decimal(agency_index) * Decimal("0.025"))
                    ).quantize(Decimal("0.01"))
                    stock = (index * 13 + supplier_index * 17 + agency_index * 29) % 110 + 8
                    if index == 20 or (index == 18 and supplier_index == 0):
                        stock = 0
                    session.add(
                        Offer(
                            supplier_id=supplier.id,
                            supplier_product_id=sp.id,
                            agency_id=agency.id,
                            price=price,
                            stock=stock,
                            preparation_minutes=30
                            + ((index + supplier_index + agency_index) % 5) * 30,
                            created_at=SNAPSHOT_DATE,
                            updated_at=SNAPSHOT_DATE,
                        )
                    )
    session.commit()


def main() -> None:
    engine = make_engine(Settings().database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed(session)
    engine.dispose()
    logging.basicConfig(level=logging.INFO)
    logging.info("Catalogue de démonstration initialisé.")


if __name__ == "__main__":
    main()
